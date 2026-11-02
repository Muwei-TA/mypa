"""Commit & communication conventions (M2-15, PRD §10.1 / v1.1·D16).

Every PR description and comment PA sends starts with the fixed first line
"本内容由自动化 Agent 生成". Review comments that were fixed are silently
marked resolved; declined ones carry a one-line reason; when the owner's
branch already contains the same change, PA closes its own PR and points at
the owner's PR with one sentence.
"""

AGENT_MARK = "本内容由自动化 Agent 生成"


def pr_body(*, task_link: str, risk: str, summary: str, details: str = "") -> str:
    """PR description template (§10.2 stage 1)."""
    lines = [
        AGENT_MARK,
        "",
        f"任务：{task_link}",
        f"风险自评：{risk}",
        "改动摘要：",
        summary,
    ]
    if details:
        lines.append(details)
    return "\n".join(lines)


def comment_body(text_: str) -> str:
    """Every comment starts with the agent mark."""
    return f"{AGENT_MARK}\n{text_}"


def review_resolution(*, fixed_issues: list[dict], declined: list[tuple[dict, str]]) -> str:
    """Map review outcomes to comment text: fixed ones are silently resolved,
    declined ones get a one-line reason each."""
    lines = [AGENT_MARK, "", "审查意见处理："]
    for issue in fixed_issues:
        mark = issue.get("severity", "minor")
        lines.append(f"- [已解决] {issue.get('message', issue)}（{mark}）")
    for issue, reason in declined:
        mark = issue.get("severity", "minor")
        lines.append(f"- [不采纳] {issue.get('message', issue)}：{reason}（{mark}）")
    return "\n".join(lines)


def duplicate_close_comment(*, owner_pr_url: str) -> str:
    """Close own PR and point at the owner's PR (D16)."""
    return (
        f"{AGENT_MARK}\n检测到该分支的改动已包含在你的 PR 中，"
        f"关闭此 PR，请以 {owner_pr_url} 为准。"
    )
