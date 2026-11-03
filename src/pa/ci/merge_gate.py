"""Merge permission matrix (PRD §10.4): 3 repo classes x 3 risks -> action."""


def repo_class(repo: str, policy) -> str:
    for name, entry in policy.repo_classes.items():
        if repo in entry.repos:
            return name
    return policy.default_class


def merge_gate(policy, *, repo: str, risk: str, checks) -> dict:
    """Return the merge decision for one PR.

    checks must expose: all_required_passed (bool) — platform-required checks
    (pa/ai-review + CI) are all green. Matrix cell actions:
    auto_release / auto_merge_notify / confirm / confirm_merge_and_release /
    confirm_no_auto_release.
    """
    klass = repo_class(repo, policy)
    cell = policy.matrix.get(klass)
    if cell is None:
        return {
            "repo_class": klass,
            "risk": risk,
            "action": "blocked",
            "reason": "merge policy is not configured",
        }
    action = getattr(cell, risk)
    if not checks.all_required_passed:
        return {
            "repo_class": klass,
            "risk": risk,
            "action": "blocked",
            "reason": "required checks not passed",
        }
    return {"repo_class": klass, "risk": risk, "action": action, "reason": ""}
