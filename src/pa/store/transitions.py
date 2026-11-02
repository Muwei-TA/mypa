import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pa.common import PAError, new_id, now
from pa.store.models import Reason

TRANSITIONS = {
    "queued": {"leased"},
    "leased": {"running", "queued"},
    "running": {"awaiting_ci", "succeeded", "failed", "awaiting_decision"},
    "awaiting_ci": {"running", "awaiting_decision", "succeeded", "failed"},
    "awaiting_decision": {"running", "suspended", "closed"},
    "failed": {"queued", "awaiting_decision"},
    "succeeded": {"judged"},
    "judged": {"closed"},
    "closed": {"running"},
    "suspended": set(),
    "cancelled": set(),
}


def transition(
    connection: Connection,
    task_id: str,
    to: str,
    expected_version: int,
    reasons: list[Reason],
    actor: str = "system",
) -> dict:
    task = (
        connection.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task_id})
        .mappings()
        .first()
    )
    if task is None:
        raise PAError("task not found", "not_found", 4)
    if task["version"] != expected_version:
        raise PAError("task version conflict", "conflict")
    allowed = TRANSITIONS[task["status"]] | {"cancelled"}
    if to == task["status"] or to not in allowed:
        raise PAError(f"illegal transition: {task['status']} -> {to}", "conflict")
    if not reasons:
        raise PAError("at least one transition reason is required")
    result = (
        connection.execute(
            text("""
        UPDATE tasks SET status=:to, version=version+1,
          closed_at=CASE WHEN :to IN ('closed','cancelled') THEN :at ELSE NULL END
        WHERE id=:id AND version=:version
        RETURNING *
    """),
            {"id": task_id, "to": to, "version": expected_version, "at": now()},
        )
        .mappings()
        .first()
    )
    if result is None:
        raise PAError("task version conflict", "conflict")
    connection.execute(
        text("""
        INSERT INTO task_events(id,task_id,from_status,to_status,reason_json,actor,at)
        VALUES(:id,:task,:from_status,:to,:reason,:actor,:at)
    """),
        {
            "id": new_id(),
            "task": task_id,
            "from_status": task["status"],
            "to": to,
            "reason": json.dumps([r.model_dump() for r in reasons]),
            "actor": actor,
            "at": now(),
        },
    )
    return dict(result)
