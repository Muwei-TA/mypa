"""CI pipeline orchestration (M2-12, PRD §10.2 / §4.3).

Stage order: opened -> ai_review -> full_check -> merge_gate -> merged ->
released -> verified -> delivered. A failed review/check re-enters opened with
fix_round + 1; three rounds escalate to awaiting_decision with the
one-more-round / take-over / close-PR card. Waiting for platform CI times out
after 30 minutes by default.
"""

from sqlalchemy import text

from pa.common import now

STAGES = (
    "opened",
    "ai_review",
    "full_check",
    "merge_gate",
    "merged",
    "released",
    "verified",
    "delivered",
)

CI_WAIT_TIMEOUT_MIN = 30


def next_stage(current: str, outcome: str) -> str:
    """outcome: pass | fix | timeout."""
    if outcome == "fix":
        return "opened"
    if outcome == "timeout":
        return "full_check"
    index = STAGES.index(current)
    return STAGES[min(index + 1, len(STAGES) - 1)]


def advance(connection, *, pr_id: str, stage: str, detail: str = "") -> dict:
    connection.execute(
        text("""
        INSERT INTO ci_runs(id,pr_id,round,provider,run_ref,conclusion,summary)
        VALUES(:id,:pr,:round,'pa.pipeline',:stage,:outcome,:summary)
    """),
        {
            "id": f"{pr_id}:{stage}:{now()}",
            "pr": pr_id,
            "round": 0,
            "stage": stage,
            "outcome": detail or stage,
            "summary": detail,
        },
    )
    return {"pr_id": pr_id, "stage": stage, "detail": detail}


def fix_round(connection, task_id: str, *, max_rounds: int = 3) -> dict:
    """Bump fix_round; at the cap escalate to awaiting_decision (M2-12)."""
    current = connection.execute(
        text("SELECT fix_round FROM tasks WHERE id=:id"), {"id": task_id}
    ).scalar_one()
    if current + 1 >= max_rounds:
        connection.execute(
            text("""
            UPDATE tasks SET fix_round=fix_round+1, status='awaiting_decision'
              WHERE id=:id
        """),
            {"id": task_id},
        )
        return {"task_id": task_id, "fix_round": current + 1, "escalated": True}
    connection.execute(
        text("UPDATE tasks SET fix_round=fix_round+1 WHERE id=:id"),
        {"id": task_id},
    )
    return {"task_id": task_id, "fix_round": current + 1, "escalated": False}


def decision_options_for_pr() -> list[dict]:
    """The three-option card after three fix rounds (PRD §10.2)."""
    return [
        {"id": "one_more_round", "label": "再给 1 轮"},
        {"id": "take_over", "label": "我接手"},
        {"id": "close_pr", "label": "关闭 PR"},
    ]
