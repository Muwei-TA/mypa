"""Overview feed (M3-07, §20.3 / v1.1·D11).

The first screen shows only items waiting on the owner — pending decisions,
PRs to review, blocked/failed tasks, failed deliveries — plus system
problems. Each row carries an action button; counts equal their lists;
healthy systems take no space.
"""

from sqlalchemy import text

OWNER_STAGES = ("owner/answer", "owner/review", "owner/blocked_failed", "owner/delivery")


def overview(connection) -> dict:
    rows = connection.execute(
        text("""
        SELECT t.id, t.status, e.summary, ts.stage FROM tasks t
        JOIN task_stage ts ON ts.id = t.id
        LEFT JOIN events e ON e.id = t.event_id
        WHERE ts.stage IN ('owner/answer','owner/review','owner/blocked_failed','owner/delivery')
        ORDER BY ts.stage, t.enqueued_at
    """)
    ).all()
    items = []
    for task_id, status, summary, stage in rows:
        row = {"task_id": task_id, "status": status, "stage": stage, "summary": summary}
        if stage == "owner/answer":
            pending = connection.execute(
                text("""
                SELECT id,question FROM decisions
                WHERE task_id=:id AND status='pending' ORDER BY rowid LIMIT 1
            """),
                {"id": task_id},
            ).first()
            row["decision_id"] = pending[0] if pending else None
            row["question"] = pending[1] if pending else None
            row["action"] = "answer"
        elif stage == "owner/review":
            row["action"] = "review_pr"
        elif stage == "owner/delivery":
            failed = connection.execute(
                text("""
                SELECT id,kind FROM effects
                WHERE task_id=:id AND status='failed' AND attempts>=3 LIMIT 1
            """),
                {"id": task_id},
            ).first()
            row["effect_id"] = failed[0] if failed else None
            row["effect_kind"] = failed[1] if failed else None
            row["action"] = "retry_or_drop"
        else:
            row["action"] = "inspect"
        items.append(row)
    # system problems: paused flag, failed engine rows, breaker open
    problems = []
    paused = connection.execute(
        text("SELECT value FROM system_state WHERE key='paused' AND value='1'")
    ).first()
    if paused:
        problems.append({"kind": "paused", "text": "全局已暂停"})
    for engine_id, state, breaker in connection.execute(
        text("""
        SELECT engine_id,status,breaker_state FROM engine_health
        WHERE breaker_state='open' OR status != 'ok'
    """)
    ).all():
        problems.append(
            {
                "kind": "engine",
                "engine_id": engine_id,
                "text": f"引擎 {engine_id} 异常（{state}/{breaker or '-'}）",
            }
        )
    counts = {stage: sum(1 for item in items if item["stage"] == stage) for stage in OWNER_STAGES}
    return {
        "items": items,
        "counts": counts,
        "problems": problems,
        "total_waiting": len(items),
    }
