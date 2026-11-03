from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.exc import OperationalError

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.config.setup import save_files
from pa.hub import pause as pa_pause
from pa.hub.pipeline import ingest
from pa.hub.ranking import fill_missing, plan_move, reindex
from pa.ingress.base import Event
from pa.store.db import make_engine, transaction
from pa.store.models import Reason
from pa.store.transitions import transition

READABLE = frozenset({"events", "tasks", "observations", "effects", "audit_log"})
ACTIVE_STATUSES = (
    "queued",
    "leased",
    "running",
    "awaiting_ci",
    "awaiting_decision",
    "failed",
    "suspended",
)
PRIORITIES = ("P0", "P1", "P2", "P3")
QUEUE_ORDER = (
    "pinned DESC, priority ASC, manual_rank ASC NULLS LAST, skill_rank ASC, enqueued_at ASC"
)


class OwnerFacade:
    """Local owner CLI only. API/agent/executor authentication is a later increment."""

    def __init__(self, config_dir: Path):
        from pa.config.versions import recover_pending

        recover_pending(config_dir)
        self.config_dir = config_dir
        self.config = load(config_dir)
        self._config_signature = self._signature()
        self.engine = make_engine(self.config.main.data_path(config_dir) / "pa.sqlite3")

    def close(self):
        self.engine.dispose()

    @contextmanager
    def writing(self, action: str):
        try:
            with transaction(self.engine) as connection:
                yield connection
                connection.execute(
                    text("""
                    INSERT INTO audit_log(id,actor,action,at) VALUES(:id,'owner',:action,:at)
                """),
                    {"id": new_id(), "action": action, "at": now()},
                )
        except OperationalError as exc:
            if "no such table" in str(exc):
                raise PAError(
                    "database is not initialized; run pa migrate", "not_found", 4
                ) from None
            if "locked" in str(exc):
                raise PAError("database is busy; retry the operation", "conflict") from None
            raise

    def emit(self, event: Event):
        with self.writing("event.emit") as connection:
            signature = self._signature()
            if signature != self._config_signature:
                refreshed = load(self.config_dir)
                if refreshed.main.data_path(self.config_dir) != self.config.main.data_path(
                    self.config_dir
                ):
                    raise PAError("data_dir changes require a stopped runtime", "conflict")
                self.config = refreshed
                self._config_signature = signature
            return ingest(connection, event, self.config)

    def _signature(self):
        import hashlib

        return tuple(
            (p.name, hashlib.sha256(p.read_bytes()).digest())
            for p in sorted(self.config_dir.glob("*.yaml"))
        )

    def list(self, table: str, limit: int = 50):
        if table not in READABLE:
            raise PAError("table is not exposed", "forbidden", 3)
        if not 1 <= limit <= 500:
            raise PAError("limit must be between 1 and 500")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT :n"),
                {
                    "n": limit,
                },
            ).mappings()
            return {"items": [dict(r) for r in rows]}

    def show(self, table: str, id_: str):
        if table not in READABLE:
            raise PAError("table is not exposed", "forbidden", 3)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text(f"SELECT * FROM {table} WHERE id=:id"),
                    {
                        "id": id_,
                    },
                )
                .mappings()
                .first()
            )
            if row is None:
                raise PAError("resource not found", "not_found", 4)
            result = dict(row)
            if table == "tasks":
                result["timeline"] = [
                    dict(r)
                    for r in connection.execute(
                        text("SELECT * FROM task_events WHERE task_id=:id ORDER BY rowid"),
                        {"id": id_},
                    ).mappings()
                ]
            return result

    def list_tasks(self, *, status=None, skill=None, limit=50, cursor=None):
        if not 1 <= limit <= 500:
            raise PAError("limit must be between 1 and 500")
        clauses, params = [], {"limit": limit + 1}
        for field, value in (("status", status), ("skill_id", skill)):
            if value is not None:
                clauses.append(f"{field}=:{field}")
                params[field] = value
        if cursor:
            clauses.append("id<:cursor")
            params["cursor"] = cursor
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    text("SELECT * FROM tasks" + where + " ORDER BY id DESC LIMIT :limit"), params
                )
                .mappings()
                .all()
            )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    def cancel(self, task_id: str):
        with self.writing("task.cancel") as connection:
            row = connection.execute(
                text("SELECT version FROM tasks WHERE id=:id"),
                {
                    "id": task_id,
                },
            ).first()
            if row is None:
                raise PAError("task not found", "not_found", 4)
            return transition(
                connection,
                task_id,
                "cancelled",
                row.version,
                [
                    Reason(code="owner_cancelled"),
                ],
                actor="owner",
            )

    def pause(self):
        with self.writing("system.pause") as connection:
            return pa_pause.pause(connection)

    def resume(self):
        with self.writing("system.resume") as connection:
            return pa_pause.resume(connection)

    def status(self):
        with self.engine.connect() as connection:
            return {**pa_pause.state(connection), "policy_version": self.config.policy_version}

    def move_task(
        self,
        task_id: str,
        *,
        before: str | None = None,
        to_top: bool = False,
        to_bottom: bool = False,
    ):
        with self.writing("task.move") as connection:
            rows = (
                connection.execute(
                    text(
                        f"SELECT id, manual_rank FROM tasks WHERE status IN :statuses "
                        f"ORDER BY {QUEUE_ORDER}"
                    ).bindparams(bindparam("statuses", expanding=True)),
                    {"statuses": ACTIVE_STATUSES},
                )
                .mappings()
                .all()
            )
            ids = [row["id"] for row in rows]
            if task_id not in ids:
                raise PAError("task is not in the active queue", "not_found", 4)
            ranks = {row["id"]: row["manual_rank"] for row in rows}
            # Ranks added for previously unranked items must be persisted too.
            filled = fill_missing(ids, ranks)
            initialized = {id_: filled[id_] for id_ in ids if ranks[id_] is None and id_ != task_id}
            _, changed = plan_move(
                ids, ranks, task_id, before=before, to_top=to_top, to_bottom=to_bottom
            )
            updates = {**initialized, **changed}
            for id_, rank in updates.items():
                connection.execute(
                    text("UPDATE tasks SET manual_rank=:rank WHERE id=:id"),
                    {"id": id_, "rank": rank},
                )
            return {"task_id": task_id, "manual_rank": changed[task_id], "changed": len(updates)}

    def set_pinned(self, task_id: str, pinned: bool):
        with self.writing("task.pin") as connection:
            result = connection.execute(
                text("UPDATE tasks SET pinned=:pinned WHERE id=:id"),
                {"id": task_id, "pinned": int(pinned)},
            )
            if not result.rowcount:
                raise PAError("task not found", "not_found", 4)
            return {"task_id": task_id, "pinned": pinned}

    def set_priority(self, task_id: str, priority: str):
        if priority not in PRIORITIES:
            raise PAError("priority must be one of P0, P1, P2, P3")
        with self.writing("task.priority") as connection:
            result = connection.execute(
                text("UPDATE tasks SET priority=:priority WHERE id=:id"),
                {"id": task_id, "priority": priority},
            )
            if not result.rowcount:
                raise PAError("task not found", "not_found", 4)
            return {"task_id": task_id, "priority": priority}

    def reorder_skills(self, skill_ids: list[str]):
        with self.writing("skill.reorder") as connection:
            known = [
                row[0]
                for row in connection.execute(
                    text("SELECT DISTINCT skill_id FROM tasks ORDER BY skill_id")
                ).all()
            ]
            if sorted(skill_ids) != sorted(known) or len(set(skill_ids)) != len(skill_ids):
                raise PAError("provide every known skill id exactly once")
            ranks = reindex(skill_ids)
            for skill_id, rank in ranks.items():
                connection.execute(
                    text("UPDATE tasks SET skill_rank=:rank WHERE skill_id=:skill"),
                    {"skill": skill_id, "rank": rank},
                )
            return {"skill_rank": ranks}

    def effect_retry(self, effect_id: str):
        """Requeue one outbox entry for delivery. Owner judgement covers the
        delivery_unknown guard: retrying acknowledges the risk of a duplicate
        send after Feishu's one-hour uuid dedup window."""
        with self.writing("effect.retry") as connection:
            row = (
                connection.execute(
                    text("SELECT status, attempts FROM effects WHERE id=:id"),
                    {"id": effect_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise PAError("effect not found", "not_found", 4)
            if row["status"] in ("done", "dropped"):
                raise PAError(f"effect is already {row['status']}", "conflict")
            connection.execute(
                text("""
                UPDATE effects SET status='pending',next_at=NULL,lease_owner=NULL,
                  lease_until=NULL,last_error=NULL WHERE id=:id
            """),
                {"id": effect_id},
            )
            return {"effect_id": effect_id, "status": "pending", "attempts": row["attempts"]}

    def effect_drop(self, effect_id: str, *, reason: str = "owner_dropped"):
        with self.writing("effect.drop") as connection:
            row = (
                connection.execute(
                    text("SELECT status FROM effects WHERE id=:id"),
                    {"id": effect_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise PAError("effect not found", "not_found", 4)
            if row["status"] in ("done", "dropped"):
                raise PAError(f"effect is already {row['status']}", "conflict")
            connection.execute(
                text("""
                UPDATE effects SET status='dropped',dropped_reason=:reason,
                  lease_owner=NULL,lease_until=NULL WHERE id=:id
            """),
                {"id": effect_id, "reason": reason},
            )
            return {"effect_id": effect_id, "status": "dropped", "reason": reason}

    def set_rule_mode(self, rule_id: str, mode: str):
        """Persist accept_mode for one rule into rules.yaml (validated + backed up)."""
        if mode not in ("off", "observe", "auto"):
            raise PAError("mode must be one of off, observe, auto")
        # Reload: the in-memory snapshot may lag behind a previous write.
        rules = load(self.config_dir).rules.model_dump(mode="json", by_alias=True)
        target = next((r for r in rules["rules"] if r["id"] == rule_id), None)
        if target is None:
            raise PAError("rule not found", "not_found", 4)
        if target["accept_mode"] == mode:
            return {"rule_id": rule_id, "accept_mode": mode, "changed": False}
        target["accept_mode"] = mode
        # Validate and replace rules.yaml first; the audit row records the completed change.
        save_files(self.config_dir, {"rules.yaml": rules})
        with self.writing("rule.mode"):
            pass
        return {"rule_id": rule_id, "accept_mode": mode, "changed": True}

    def reorder_rules(self, rule_ids: list[str]):
        """Persist the new rule match order into rules.yaml (M3-08).

        The full id list must be provided exactly once each; the YAML list
        order is rewritten to match and the change is audited.
        """
        rules = load(self.config_dir).rules.model_dump(mode="json", by_alias=True)
        known = [r["id"] for r in rules["rules"]]
        if sorted(rule_ids) != sorted(known) or len(set(rule_ids)) != len(rule_ids):
            raise PAError("provide every known rule id exactly once")
        if rule_ids == known:
            return {"rule_order": rule_ids, "changed": False}
        by_id = {r["id"]: r for r in rules["rules"]}
        rules["rules"] = [by_id[rid] for rid in rule_ids]
        save_files(self.config_dir, {"rules.yaml": rules})
        with self.writing("rule.reorder"):
            pass
        return {"rule_order": rule_ids, "changed": True}
