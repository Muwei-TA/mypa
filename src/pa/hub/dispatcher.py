import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import new_id, now
from pa.hub.pause import state
from pa.store.db import transaction
from pa.store.models import Reason
from pa.store.transitions import transition


def claim(engine, config, engine_id):
    owner = new_id()
    with transaction(engine) as c:
        if state(c)["paused"]:
            return None
        active = (
            c.execute(text("SELECT * FROM tasks WHERE status IN ('leased','running')"))
            .mappings()
            .all()
        )
        normal = sum(not task["reserved_slot"] for task in active)
        reserved = normal >= config.main.worker.slots
        if (
            reserved
            and sum(bool(t["reserved_slot"]) for t in active)
            >= config.main.worker.reserved_owner_slots
        ):
            return None
        engine_active = c.execute(
            text("""
            SELECT count(*) FROM runs r JOIN tasks t ON t.id=r.task_id
            WHERE r.engine_id=:engine AND r.ended_at IS NULL AND t.status IN ('leased','running')
        """),
            {"engine": engine_id},
        ).scalar_one()
        if engine_active >= config.engines.engines[engine_id].max_concurrency:
            return None
        candidates = (
            c.execute(
                text("""
            SELECT * FROM tasks WHERE status='queued' AND skill_id='sample-direct'
            ORDER BY pinned DESC,priority ASC,manual_rank ASC NULLS LAST,
              skill_rank ASC,enqueued_at ASC
        """)
            )
            .mappings()
            .all()
        )
        for candidate in candidates:
            mandate = json.loads(candidate["mandate_json"])
            if reserved and mandate["strength"] != "explicit":
                continue
            if any(t["skill_id"] == candidate["skill_id"] for t in active):
                continue  # sample-direct has concurrency=1.
            if any(
                (
                    candidate["concurrency_key"]
                    and t["concurrency_key"] == candidate["concurrency_key"]
                )
                or (candidate["repo"] and t["repo"] == candidate["repo"])
                for t in active
            ):
                continue
            current = transition(
                c, candidate["id"], "leased", candidate["version"], [Reason(code="claimed")]
            )
            c.execute(
                text("""
                UPDATE tasks SET lease_owner=:owner,lease_until=:until,reserved_slot=:reserved
                WHERE id=:id
            """),
                {
                    "id": candidate["id"],
                    "owner": owner,
                    "reserved": int(reserved),
                    "until": (
                        datetime.now(UTC)
                        + timedelta(seconds=config.engines.engines[engine_id].timeout_s + 60)
                    ).isoformat(),
                },
            )
            current = transition(
                c, candidate["id"], "running", current["version"], [Reason(code="started")]
            )
            current["lease_owner"] = owner
            return current
    return None


def recover_expired(engine):
    recovered = []
    with transaction(engine) as c:
        for task in (
            c.execute(
                text("""
            SELECT * FROM tasks WHERE status IN ('leased','running') AND lease_until<:now
            ORDER BY enqueued_at
        """),
                {"now": now()},
            )
            .mappings()
            .all()
        ):
            current = dict(task)
            if current["status"] == "running":
                current = transition(
                    c, current["id"], "failed", current["version"], [Reason(code="lease_expired")]
                )
            transition(
                c, current["id"], "queued", current["version"], [Reason(code="lease_recovered")]
            )
            c.execute(
                text("UPDATE tasks SET lease_owner=NULL,lease_until=NULL WHERE id=:id"),
                {"id": task["id"]},
            )
            c.execute(
                text(
                    "UPDATE runs SET recovered=1,ended_at=:at "
                    "WHERE task_id=:id AND ended_at IS NULL"
                ),
                {"id": task["id"], "at": now()},
            )
            recovered.append(task["id"])
    return recovered
