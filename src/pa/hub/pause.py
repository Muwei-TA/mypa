from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pa.common import now


def state(connection: Connection) -> dict:
    values = dict(connection.execute(text("SELECT key,value FROM system_state")).all())
    return {"paused": values.get("paused") == "1", "paused_at": values.get("paused_at")}


def set_value(connection: Connection, key: str, value: str) -> None:
    connection.execute(
        text("""
        INSERT INTO system_state(key,value,updated_at) VALUES(:key,:value,:at)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    """),
        {"key": key, "value": value, "at": now()},
    )


def pause(connection: Connection) -> dict:
    if not state(connection)["paused"]:
        set_value(connection, "paused", "1")
        set_value(connection, "paused_at", now())
    return state(connection)


def resume(connection: Connection) -> dict:
    current = state(connection)
    if current["paused"]:
        duration = datetime.now(UTC) - datetime.fromisoformat(current["paused_at"])
        duration = max(duration, timedelta())
        pending = connection.execute(
            text(
                "SELECT id,expires_at FROM decisions "
                "WHERE status='pending' AND expires_at IS NOT NULL"
            )
        ).all()
        for id_, expires_at in pending:
            shifted = datetime.fromisoformat(expires_at) + duration
            connection.execute(
                text("UPDATE decisions SET expires_at=:at WHERE id=:id"),
                {
                    "id": id_,
                    "at": shifted.isoformat(),
                },
            )
        set_value(connection, "paused", "0")
        set_value(connection, "paused_at", "")
    return state(connection)
