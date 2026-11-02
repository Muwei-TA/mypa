"""Mandate re-validation (M2-06, PRD §6.5 / v1.1·D3): immutable acceptance record.

Checks before resume or re-dispatch:
- inferred mandates: the matched rule is still enabled AND still in auto mode;
- explicit mandates: the initiator is still in the owner allowlist.
Failed validation voids the mandate; the message re-enters as a new event.
"""

import json

from sqlalchemy import text


def revalidate(connection, *, mandate_json: str, config, actor: str | None = None) -> dict:
    mandate = json.loads(mandate_json)
    strength = mandate.get("strength", "inferred")
    if strength == "explicit":
        if actor is None:
            actor = mandate.get("actor", "")
        allowed = _allowlisted(connection, config, actor)
        if not allowed:
            return {"valid": False, "reason": "initiator_not_allowlisted"}
        return {"valid": True}
    matched = mandate.get("matched_rules", [])
    for rule in config.rules.rules:
        if rule.id in matched:
            if not rule.enabled:
                return {"valid": False, "reason": "rule_disabled", "rule": rule.id}
            if rule.accept_mode != "auto":
                return {"valid": False, "reason": "mode_not_auto", "rule": rule.id}
    if not matched:
        return {"valid": False, "reason": "no_matched_rules"}
    return {"valid": True}


def _allowlisted(connection, config, actor: str) -> bool:
    if not actor:
        return False
    owner = config.principals.owner
    known = {owner.github, owner.gitea, owner.feishu_open_id}
    if actor in known:
        return True
    # Canonical identity form github:<login> / gitea:<login>.
    if actor.startswith("github:") and owner.github == actor.split(":", 1)[1]:
        return True
    if actor.startswith("gitea:") and owner.gitea == actor.split(":", 1)[1]:
        return True
    return False


def void_mandate(connection, task_id: str, reason: str):
    """Record the voiding without touching the immutable mandate itself."""
    from pa.common import new_id, now

    connection.execute(
        text("""
        INSERT INTO task_events(id,task_id,from_status,to_status,reason_json,at)
        VALUES(:id,:task,'mandate','mandate_voided',:reason,:at)
    """),
        {
            "id": new_id(),
            "task": task_id,
            "reason": json.dumps([{"code": "mandate_voided", "detail": reason}]),
            "at": now(),
        },
    )
