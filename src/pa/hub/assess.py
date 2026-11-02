"""Model-based assessment (M5-04, PRD §6.3/v1.1·D5).

For events that would create inferred tasks (assess: true rules), the
assessor asks the router model: which enabled proactive rules match this
event, with per-rule reasons. Inferred events need at least one rule hit
to be accepted; the verdict can never widen what the deterministic gate
already allows (it can only narrow).
"""

import json

ASSESS_PROMPT = """你是事件研判器。判断下面每条已启用规则是否匹配该事件。
只输出 JSON：{{"results":[{{"rule_id":"…","match":true/false,"reason":"…"}}]}}
事件：{event}
规则：{rules}
"""


def build_prompt(event: dict, rules: list[dict]) -> str:
    return ASSESS_PROMPT.format(
        event=json.dumps(event, ensure_ascii=False),
        rules=json.dumps(rules, ensure_ascii=False),
    )


def parse_verdict(payload: dict | None, *, allowed_rule_ids: set[str]) -> dict:
    """Validate the model verdict against the deterministic surface.

    Unknown rule ids and malformed rows are dropped, never trusted. The
    accepted set is the intersection with rules the deterministic matcher
    already considered; it cannot grow here (D5).
    """
    if not isinstance(payload, dict):
        return {"ok": False, "results": [], "error": "malformed verdict"}
    rows = payload.get("results")
    if not isinstance(rows, list):
        return {"ok": False, "results": [], "error": "missing results"}
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rule_id = row.get("rule_id")
        if rule_id not in allowed_rule_ids:
            continue  # model may not invent rule hits
        results.append(
            {
                "rule_id": rule_id,
                "match": bool(row.get("match")),
                "reason": str(row.get("reason", ""))[:200],
            }
        )
    return {"ok": True, "results": results}


def decide(event: dict, rules: list[dict], model_call) -> dict:
    """Full assessment: one router-model call, verdict clamped to the gate."""
    allowed = {r["id"] for r in rules}
    response = model_call(build_prompt(event, rules))
    verdict = parse_verdict(response, allowed_rule_ids=allowed)
    if not verdict["ok"]:
        return {**verdict, "accepted": False, "reason_code": "assessment_malformed"}
    hits = [r for r in verdict["results"] if r["match"]]
    accepted = bool(hits)  # inferred events need at least one rule hit
    return {
        **verdict,
        "accepted": accepted,
        "reason_code": "assessment_hit" if accepted else "assessment_no_match",
        "matched_rules": [r["rule_id"] for r in hits],
    }
