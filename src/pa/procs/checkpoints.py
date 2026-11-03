"""Persist completed execution before judgement; pause never discards the result."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import new_id, now
from pa.hub.pause import state
from pa.obs.redact import redact
from pa.store.db import transaction


def clear(connection, task):
    connection.execute(
        text("DELETE FROM worker_checkpoints WHERE task_id=:id AND owner=:owner"),
        {"id": task["id"], "owner": task["lease_owner"]},
    )


def save(connection, task, payload):
    current = (
        connection.execute(
            text("SELECT status,lease_owner FROM tasks WHERE id=:id"), {"id": task["id"]}
        )
        .mappings()
        .one()
    )
    if current["status"] != "running" or current["lease_owner"] != task["lease_owner"]:
        return False
    until = (datetime.now(UTC) + timedelta(seconds=90)).isoformat()
    connection.execute(
        text("""
        INSERT INTO worker_checkpoints(task_id,owner,lease_until,payload_json)
        VALUES(:id,:owner,:until,:payload)
        ON CONFLICT(task_id) DO UPDATE SET owner=excluded.owner,
          lease_until=excluded.lease_until,payload_json=excluded.payload_json
        """),
        {
            "id": task["id"],
            "owner": task["lease_owner"],
            "until": until,
            "payload": json.dumps(redact(payload), ensure_ascii=False),
        },
    )
    connection.execute(
        text("UPDATE tasks SET lease_until=:until,lease_engine_id=NULL WHERE id=:id"),
        {"id": task["id"], "until": until},
    )
    return True


def hold(connection, task, payload=None):
    if payload is not None and not save(connection, task, payload):
        return None
    connection.execute(
        text("UPDATE worker_checkpoints SET lease_until=NULL WHERE task_id=:id AND owner=:owner"),
        {"id": task["id"], "owner": task["lease_owner"]},
    )
    connection.execute(
        text(
            "UPDATE tasks SET lease_until=NULL,lease_engine_id=NULL WHERE id=:id "
            "AND status='running' AND lease_owner=:owner"
        ),
        {"id": task["id"], "owner": task["lease_owner"]},
    )
    return {"task_id": task["id"], "status": "running", "deferred": True}


def defer_if_paused(connection, task, payload=None):
    if state(connection)["paused"]:
        return hold(connection, task, payload)
    return None


def take(engine):
    """Claim one persisted continuation before recovering leases or dispatching new work."""
    with transaction(engine) as connection:
        connection.execute(
            text("""
            DELETE FROM worker_checkpoints WHERE task_id IN (
                SELECT t.id FROM tasks t JOIN worker_checkpoints cp ON cp.task_id=t.id
                WHERE t.status<>'running' OR t.lease_owner IS NOT cp.owner)
        """)
        )
        if state(connection)["paused"]:
            return None
        checkpoint = (
            connection.execute(
                text(
                    "SELECT * FROM worker_checkpoints WHERE lease_until IS NULL OR lease_until<:at "
                    "ORDER BY rowid LIMIT 1"
                ),
                {"at": now()},
            )
            .mappings()
            .first()
        )
        if checkpoint is None:
            return None
        owner = new_id()
        until = (datetime.now(UTC) + timedelta(seconds=90)).isoformat()
        connection.execute(
            text("UPDATE worker_checkpoints SET owner=:owner,lease_until=:until WHERE task_id=:id"),
            {"id": checkpoint["task_id"], "owner": owner, "until": until},
        )
        task = (
            connection.execute(
                text(
                    "UPDATE tasks SET lease_owner=:owner,lease_until=:until,lease_engine_id=NULL "
                    "WHERE id=:id RETURNING *"
                ),
                {"id": checkpoint["task_id"], "owner": owner, "until": until},
            )
            .mappings()
            .one()
        )
        return dict(task), json.loads(checkpoint["payload_json"])
