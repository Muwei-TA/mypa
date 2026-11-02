"""C-tier hard limits for GUI engines (M4-05, PRD §8.4).

These are hard rules that config must not relax: C-tier tasks can never
edit code, never use irreversible tools, cap confidence at 0.6, and only
auto-close when the Skill explicitly opts in via c_tier_auto_close.
"""

from pa.common import PAError

CONFIDENCE_CAP = 0.6
FORBIDDEN_TOOLS = frozenset({"external_message", "spend_money"})


def check_c_tier_limits(*, skill, tools: list[str] | None = None) -> dict:
    """Validate a C-tier dispatch against the hard rules.

    Returns the effective limits dict; raises PAError on violation.
    The tool list defaults to the skill's own tools; the explicit argument
    only widens the check, never narrows it.
    """
    ceiling = getattr(skill, "risk_ceiling", None)
    if ceiling != "low":
        raise PAError("C-tier engine requires skill risk_ceiling=low (hard rule)", "forbidden", 3)
    if getattr(skill, "repo_write", False):
        raise PAError("C-tier engines cannot edit code (can_edit_code=false)", "forbidden", 3)
    skill_tools = list(getattr(skill, "tools", None) or [])
    for tool in skill_tools + list(tools or []):
        if tool in FORBIDDEN_TOOLS:
            raise PAError(f"C-tier tasks cannot use {tool} (hard rule)", "forbidden", 3)
    return {
        "can_edit_code": False,
        "confidence_cap": CONFIDENCE_CAP,
        "forbidden_tools": sorted(FORBIDDEN_TOOLS),
        "auto_close": bool(getattr(skill, "c_tier_auto_close", False)),
    }


def clamp_confidence(value: float | None) -> float | None:
    """Clamp any C-tier structured conversion confidence to the cap."""
    if value is None:
        return None
    return min(value, CONFIDENCE_CAP)
