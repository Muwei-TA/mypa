from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.hub import pause as pa_pause
from pa.hub.pipeline import ingest
from pa.ingress.base import Event
from pa.store.db import make_engine, transaction
from pa.store.models import Reason
from pa.store.transitions import transition

READABLE = frozenset({"events", "tasks", "observations", "effects", "audit_log"})


class OwnerFacade:
    """Local owner CLI only. API/agent/executor authentication is a later increment."""

    def __init__(self, config_dir: Path):
        self.config = load(config_dir)
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
            return ingest(connection, event, self.config)

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
