"""Cost ledger and four-layer budgets (M5-07, PRD §15).

estimate() checks global / skill / engine / task budgets before a call;
record() writes actual usage afterwards. NULL limit_cny means meter-only,
never block. 80% triggers a P1 warning plus cheap-fallback preference for
P2/P3 tasks; 100% pauses dispatch for the over-budget scope only, owner
reserved slots keep flowing. Single-task over-limit cancels the run as
"没跑成" regardless of money limits.
"""

from sqlalchemy import text


def record(
    connection,
    *,
    task_id: str,
    run_id: str | None = None,
    engine_id: str | None = None,
    skill_id: str | None = None,
    mode: str = "token",
    input_tokens: int = 0,
    output_tokens: int = 0,
    calls: int = 1,
    duration_s: float = 0.0,
    cost_cny: float = 0.0,
    est: bool = False,
) -> str:
    from pa.common import new_id, now

    entry_id = new_id()
    connection.execute(
        text("""
        INSERT INTO cost_ledger(id,task_id,run_id,engine_id,skill_id,mode,
          input_tokens,output_tokens,calls,duration_s,cost_cny,est,at)
        VALUES(:id,:task,:run,:engine,:skill,:mode,:in,:out,:calls,:dur,:cost,:est,:at)
    """),
        {
            "id": entry_id,
            "task": task_id,
            "run": run_id,
            "engine": engine_id,
            "skill": skill_id,
            "mode": mode,
            "in": input_tokens,
            "out": output_tokens,
            "calls": calls,
            "dur": duration_s,
            "cost": cost_cny,
            "est": int(est),
            "at": now(),
        },
    )
    return entry_id


_SCOPE_COLUMNS = {
    "global": "task_id",
    "skill": "skill_id",
    "engine": "engine_id",
    "task": "task_id",
}


def _scope_totals(connection, scope: str, scope_id: str | None, period: str) -> float:
    row = connection.execute(
        text("""
        SELECT COALESCE(SUM(cost_cny),0) FROM cost_ledger
        WHERE est=0 AND at >= date('now', :start)
          AND (:sid IS NULL OR
               (scope_column = :sid))
    """.replace("scope_column", _SCOPE_COLUMNS[scope])),
        {
            "start": "-1 day" if period == "day" else "-1 month",
            "sid": scope_id,
        },
    ).first()
    return float(row[0]) if row else 0.0


def check_budgets(connection, *, skill_id: str | None, engine_id: str | None, task_id: str) -> dict:
    """Evaluate the four budget layers for one prospective call.

    Returns per-scope status: ok | soft (>=80%) | blocked (>=100%).
    NULL limits never block (meter only).
    """
    result = {}
    rows = connection.execute(
        text("SELECT scope,scope_id,period,limit_cny,soft_pct FROM budgets")
    ).mappings()
    for budget in rows:
        scope = budget["scope"]
        if scope == "skill" and budget["scope_id"] != skill_id:
            continue
        if scope == "engine" and budget["scope_id"] != engine_id:
            continue
        if scope == "task":
            continue  # task limits are token/turn/time, not money; handled elsewhere
        total = _scope_totals(connection, scope, budget["scope_id"], budget["period"])
        limit = budget["limit_cny"]
        if limit is None:
            result[scope] = {"status": "ok", "meter_only": True, "spent": total}
            continue
        ratio = total / limit if limit > 0 else 1.0
        if ratio >= 1.0:
            result[scope] = {"status": "blocked", "spent": total, "limit": limit}
        elif ratio >= (budget["soft_pct"] or 80) / 100:
            result[scope] = {"status": "soft", "spent": total, "limit": limit}
        else:
            result[scope] = {"status": "ok", "spent": total, "limit": limit}
    # global layer always present in the verdict
    result.setdefault("global", {"status": "ok", "spent": 0.0})
    return result


def budget_action(status: dict, *, task_priority: str) -> dict:
    """Map a check_budgets verdict to the §15 actions.

    soft: P1 warning + cheap fallback for P2/P3 tasks.
    blocked: pause_queue for that scope, owner slots keep flowing.
    """
    actions = {"warnings": [], "use_cheap_fallback": False, "pause_scope": None}
    for scope, info in status.items():
        if info.get("status") == "soft":
            actions["warnings"].append(f"budget_soft:{scope}")
            if task_priority in ("P2", "P3"):
                actions["use_cheap_fallback"] = True
        if info.get("status") == "blocked":
            actions["pause_scope"] = scope
    return actions
