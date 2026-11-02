"""Execution environment variables (M2-05, PRD §7.2.1): the 11 PA_* vars.

No credentials ever enter this mapping; blocked_commands is documented as a
default constraint only, not a security boundary.
"""

VAR_NAMES = (
    "PA_TASK_ID",
    "PA_TASK_NAME",
    "PA_WORKSPACE",
    "PA_SKILL",
    "PA_RULE",
    "PA_REPO",
    "PA_SCM",
    "PA_ISSUE",
    "PA_PR_HEAD",
    "PA_BRANCH",
    "PA_BLOCKED_COMMANDS",
)


def build_env(
    *,
    task_id: str,
    task_name: str,
    workspace: str,
    skill: str,
    rule: str,
    repo: str | None,
    scm: str | None,
    issue: int | None,
    pr_head: str | None,
    branch: str | None,
    blocked_commands: list[str] | None = None,
) -> dict[str, str]:
    values = {
        "PA_TASK_ID": task_id,
        "PA_TASK_NAME": task_name,
        "PA_WORKSPACE": workspace,
        "PA_SKILL": skill,
        "PA_RULE": rule,
        "PA_REPO": repo or "",
        "PA_SCM": scm or "",
        "PA_ISSUE": str(issue) if issue else "",
        "PA_PR_HEAD": pr_head or "",
        "PA_BRANCH": branch or "",
        "PA_BLOCKED_COMMANDS": ",".join(blocked_commands or []),
    }
    return values


def filter_env(full_env: dict[str, str], injected: dict[str, str]) -> dict[str, str]:
    """Engine subprocesses see PATH & locale basics plus exactly the 11 vars.

    Everything else from the parent environment (credentials included) is
    dropped.
    """
    allowed = {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR"}
    merged = {k: v for k, v in full_env.items() if k in allowed}
    for key in VAR_NAMES:
        if key in injected:
            merged[key] = injected[key]
    return merged
