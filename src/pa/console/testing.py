"""Rule and skill dry-run testing (M3-11, PRD §20.1/§20.3).

`dry_run_event` runs one example event through rule matching without
executing anything: it reports every rule that matches (in configured
order), the accept mode in force, and the skill the first match would
dispatch to. This backs `POST /rules:test`, `pa rule test` and
`pa skill test`.
"""

from pa.config.loader import load
from pa.hub.router import matches
from pa.ingress.base import Event


def dry_run_event(payload: dict) -> Event:
    """Accept a full event dict or a bare payload and fill sensible defaults."""
    from datetime import UTC, datetime

    from pa.common import new_id

    body = dict(payload)
    known = {"id", "source", "type", "category", "actor", "subject", "payload"}
    if not (set(body) & known):
        body = {"type": body.pop("event_type", "manual.test"), "payload": body}
    return Event(
        id=body.get("id") or new_id(),
        source=body.get("source", "manual"),
        type=body.get("type", "manual.test"),
        category=body.get("category", "new_source"),
        actor=body.get("actor"),
        subject=body.get("subject"),
        payload=body.get("payload") or {},
        dedup_key=body.get("dedup_key") or f"rule-test:{new_id()}",
        received_at=body.get("received_at") or datetime.now(UTC),
        trust=body.get("trust", "unverified"),
        summary=(body.get("summary") or "rule test event")[:80],
        link=body.get("link"),
    )


def dry_run_rules(config_dir, event: dict) -> dict:
    """Match one event against every enabled rule; report hits in order.

    Returns {"matched": [...], "first": {...}|None, "policy_version": "..."}
    where each hit carries rule_id, strength, accept_mode, would ("create_task"
    or "skip"), skill and priority. No task is created, no effect emitted.
    """
    loaded = load(config_dir)
    candidate = dry_run_event(event)
    hits = []
    for rule in sorted(loaded.rules.rules, key=lambda r: r.rank):
        if not rule.enabled:
            continue
        if not matches(rule, candidate):
            continue
        mode = rule.accept_mode or loaded.main.defaults.accept_mode_proactive
        explicit = (
            candidate.source == "manual"
            or candidate.addressed
            or bool(rule.require.actor_in)
        )
        hits.append(
            {
                "rule_id": rule.id,
                "strength": "explicit" if explicit else "implicit",
                "accept_mode": mode,
                "would": "create_task" if mode != "off" else "skip",
                "skill": rule.skill,
                "priority": rule.priority,
                "continue": rule.continue_,
            }
        )
        if not rule.continue_:
            break
    first_skill = hits[0]["skill"] if hits else None
    return {
        "matched": hits,
        "first": {"skill": first_skill, "priority": hits[0]["priority"]} if hits else None,
        "policy_version": loaded.policy_version,
    }


def dry_run_skill(config_dir, skill_id: str, input_payload: dict | None = None) -> dict:
    """Dry-run one skill: which rules dispatch to it, plus assembled metadata.

    Reports the rules that would route an event to this skill and the skill
    yaml as validated. No execution happens.
    """
    loaded = load(config_dir)
    found = [r for r in loaded.rules.rules if r.skill == skill_id]
    if not found:
        return {"skill": skill_id, "found": False, "rules": [], "input_sample": input_payload or {}}
    sample = dict(input_payload or {})
    candidate = dry_run_event(sample)
    rules_out = []
    for rule in sorted(found, key=lambda r: r.rank):
        rules_out.append(
            {
                "rule_id": rule.id,
                "rank": rule.rank,
                "matches_sample": matches(rule, candidate) if sample else None,
                "accept_mode": rule.accept_mode or loaded.main.defaults.accept_mode_proactive,
            }
        )
    return {
        "skill": skill_id,
        "found": True,
        "rules": rules_out,
        "input_sample": sample,
        "policy_version": loaded.policy_version,
    }
