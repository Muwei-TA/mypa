"""Thread subscriptions and task resumption (M2-07, PRD §6.2.1 / D7 / D8).

Subscriptions route your comments on subscribed threads straight to the task,
bypassing rule matching. Ended tasks resume with the original mandate, worktree
and branch after revalidation; bots and CI never wake an ended task.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import new_id, now
from pa.hub.mandate import revalidate

BOT_SUFFIXES = ("[bot]", "-bot", "_bot")
CI_LOGINS = ("github-actions", "gitea-actions")


def is_bot(actor: str) -> bool:
    lowered = actor.lower()
    login = lowered.split(":", 1)[1] if ":" in lowered else lowered
    if login in CI_LOGINS:
        return True
    return any(login.endswith(suffix) for suffix in BOT_SUFFIXES)


def subscribe(connection, *, task_id: str, thread_ref: str, context_only: bool = False) -> str:
    """Insert unless this thread already has a live subscription."""
    existing = connection.execute(
        text("SELECT id FROM subscriptions WHERE thread_ref=:thread"),
        {"thread": thread_ref},
    ).scalar_one_or_none()
    if existing:
        return existing
    subscription_id = new_id()
    connection.execute(
        text("""
        INSERT INTO subscriptions(id,task_id,scm_or_source,thread_ref,own,
          context_only,created_at,expires_at)
        VALUES(:id,:task,'scm',:thread,:own,:context,:at,:expires)
    """),
        {
            "id": subscription_id,
            "task": task_id,
            "thread": thread_ref,
            "own": int(not context_only),
            "context": int(context_only),
            "at": now(),
            "expires": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
        },
    )
    return subscription_id


def route(connection, config, *, thread_ref: str, actor: str, body: str) -> dict:
    """Route a comment on a subscribed thread (D7/D8).

    Returns: deliver (task running), resume (ended task revalidated),
    context_only (no authority), reenter (mandate failed: new event) or none.
    """
    row = (
        connection.execute(
            text("""
            SELECT s.id AS sub_id, s.task_id, s.context_only, s.expires_at,
              t.status, t.mandate_json, t.repo, t.stopped_by_owner
            FROM subscriptions s JOIN tasks t ON t.id=s.task_id
            WHERE s.thread_ref=:thread
            ORDER BY s.created_at DESC LIMIT 1
        """),
            {"thread": thread_ref},
        )
        .mappings()
        .first()
    )
    if row is None:
        return {"action": "none", "reason": "no subscription"}
    if row["expires_at"] and row["expires_at"] < now():
        return {"action": "none", "reason": "subscription expired"}
    if is_bot(actor):
        if row["status"] in ("closed", "judged", "succeeded", "cancelled"):
            return {"action": "none", "reason": "bot cannot wake ended task"}
        return {"action": "context_only", "task_id": row["task_id"], "reason": "bot context"}
    if row["context_only"]:
        return {
            "action": "context_only",
            "task_id": row["task_id"],
            "reason": "subscription carries no authority",
        }
    ended = row["status"] in ("closed", "judged", "succeeded", "cancelled")
    if ended:
        if row["stopped_by_owner"]:
            return {"action": "none", "reason": "stopped by owner"}
        check = revalidate(
            connection, mandate_json=row["mandate_json"], config=config, actor=actor
        )
        if not check["valid"]:
            return {"action": "reenter", "task_id": row["task_id"], "reason": check["reason"]}
        return {
            "action": "resume",
            "task_id": row["task_id"],
            "repo": row["repo"],
            "reason": "revalidated",
        }
    return {"action": "deliver", "task_id": row["task_id"], "reason": "task running"}


def prune_expired(connection, *, now_iso: str | None = None) -> int:
    at = now_iso or now()
    result = connection.execute(
        text("""
        UPDATE subscriptions SET closed_at=:at WHERE closed_at IS NULL AND expires_at<:at2
          AND EXISTS (SELECT 1 FROM tasks t WHERE t.id=subscriptions.task_id
            AND t.status IN ('closed','judged','succeeded','cancelled'))
    """),
        {"at": at, "at2": at},
    )
    return result.rowcount
