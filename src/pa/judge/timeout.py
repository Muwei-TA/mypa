"""Decision timeout (M2-18, PRD §11).

A pending decision unanswered for 24h takes the conservative action: no
merge, no release, no irreversible tools. The task moves to ``suspended``,
exactly one reminder is sent, and timeouts of dependent decisions are
deferred while the task stays paused.
"""

import json

from sqlalchemy import text

from pa.common import new_id, now
from pa.notify.dispatch import request_notification
from pa.notify.levels import P1

REMINDER_KEY = "decision_timeout_reminded:{decision_id}"


def expire_due(
    connection,
    *,
    config,
    task_ids: set[str] | None = None,
) -> list[dict]:
    """Flip expired pending decisions to ``expired`` and suspend their tasks.

    Returns the expiry records for the caller's audit/summary use. The status
    trigger on ``tasks`` rejects invalid transitions, so an already-closed
    task simply keeps its terminal state (row untouched).
    """
    expired = []
    rows = connection.execute(
        text("""
        SELECT d.id,d.task_id,d.question,d.expires_at,t.status
        FROM decisions d LEFT JOIN tasks t ON t.id=d.task_id
        WHERE d.status='pending' AND d.expires_at IS NOT NULL AND d.expires_at <= :now
    """),
        {"now": now()},
    ).all()
    for decision_id, task_id, question, expires_at, task_status in rows:
        if task_ids is not None and task_id not in task_ids:
            continue
        connection.execute(
            text("UPDATE decisions SET status='expired' WHERE id=:id AND status='pending'"),
            {"id": decision_id},
        )
        record = {
            "decision_id": decision_id,
            "task_id": task_id,
            "question": question,
            "expires_at": expires_at,
        }
        if task_id and task_status not in ("closed", "cancelled", "succeeded", "suspended"):
            connection.execute(
                text("""
                INSERT INTO task_events(id,task_id,from_status,to_status,reason_json,actor,at)
                VALUES(:id,:task,:from_status,'suspended',:reason,'system',:at)
            """),
                {
                    "id": new_id(),
                    "task": task_id,
                    "from_status": task_status,
                    "reason": json.dumps(
                        [{"code": "decision_timeout", "values": {"decision_id": decision_id}}]
                    ),
                    "at": now(),
                },
            )
            connection.execute(
                text("UPDATE tasks SET status='suspended' WHERE id=:id"), {"id": task_id}
            )
            record["task_suspended"] = True
        else:
            record["task_suspended"] = False
        expired.append(record)
        _remind_once(connection, config=config, decision_id=decision_id, question=question)
    return expired


def _remind_once(connection, *, config, decision_id: str, question: str) -> bool:
    """Send at most one reminder per decision (system_state flag)."""
    key = REMINDER_KEY.format(decision_id=decision_id)
    row = connection.execute(
        text("SELECT value FROM system_state WHERE key=:key"), {"key": key}
    ).scalar_one_or_none()
    if row:
        return False
    connection.execute(
        text("""
        INSERT INTO system_state(key,value,updated_at) VALUES(:key,'1',:at)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    """),
        {"key": key, "at": now()},
    )
    request_notification(
        connection,
        config=config,
        task_id=None,
        level=P1,
        text_=f"决定已超时并暂停任务：{question}",
    )
    return True
