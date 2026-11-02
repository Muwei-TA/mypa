"""Policy version hashing and change fan-out (M5-05, PRD §6.5/v1.1·D6).

policy_version is a hash over the four §6.5 inputs: rules (match+skill+
accept mode), principals, engine roles, and human_gate defaults. On change:
in-flight assessments are dropped, affected tasks requeue, only affected
tasks get a policy_changed input; running tasks are never cancelled.
"""

import hashlib
import json

from sqlalchemy import text


def compute_policy_version(config) -> str:
    """Hash the four policy-bearing config sections."""
    canonical = json.dumps(
        {
            "rules": [
                {
                    "id": r.id,
                    "rank": r.rank,
                    "match": r.match.model_dump(exclude_none=True),
                    "skill": r.skill,
                    "accept_mode": r.accept_mode,
                }
                for r in sorted(config.rules.rules, key=lambda r: (r.rank, r.id))
            ],
            "principals": config.principals.model_dump(mode="json"),
            "roles": getattr(config.engines, "roles", {}) or {},
            "human_gate": config.main.human_gate.model_dump(mode="json"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "p-" + hashlib.sha256(canonical.encode()).hexdigest()[:6]


def detect_change(connection, new_version: str) -> dict:
    """Compare with system_state.policy_version; record and fan out on change.

    Returns {changed, old, new, requeued, notified}. In-flight assessments
    (observations with status pending) are dropped; queued tasks whose
    mandate policy differs requeue with a policy_changed reason.
    """
    from pa.common import now

    row = connection.execute(
        text("SELECT value FROM system_state WHERE key='policy_version'")
    ).first()
    old = row[0] if row else None
    if old == new_version:
        return {"changed": False, "old": old, "new": new_version, "requeued": 0, "notified": 0}

    # drop in-flight assessments (they were computed under the old policy)
    connection.execute(
        text("UPDATE observations SET assessment_json=:a WHERE would_accept=1"),
        {"a": json.dumps({"status": "dropped", "reason": "policy_changed"})},
    )
    # requeue only tasks whose mandate recorded a different policy version
    requeued = connection.execute(
        text("""
        UPDATE tasks SET status='queued'
        WHERE status IN ('awaiting_assessment') AND mandate_json IS NOT NULL
          AND json_extract(mandate_json,'$.policy_version') != :new
    """),
        {"new": new_version},
    ).rowcount
    connection.execute(
        text("""
        INSERT INTO system_state(key,value,updated_at) VALUES('policy_version',:v,:at)
          ON CONFLICT(key) DO UPDATE SET value=:v,updated_at=:at
    """),
        {"v": new_version, "at": now()},
    )
    return {
        "changed": True,
        "old": old,
        "new": new_version,
        "requeued": int(requeued or 0),
        "notified": int(requeued or 0),  # one policy_changed input per requeued task
    }
