"""AI review (M2-11, PRD §10.2 / K4 / K6): diff + issue in, verdict out.

Runs in a degraded no-network no-secrets mode during M2 (container lands in
M5-02). Records land in the reviews table with token_ratio; the result is
written back as the pa/ai-review commit status via the ScmAdapter.
"""

import json

from sqlalchemy import text

from pa.common import new_id, now


def review_verdict(issues: list[dict]) -> dict:
    """severity: blocker | major | minor. blocker/major -> fix rounds."""
    if not issues:
        return {"issues": [], "severity": None, "verdict": "approve"}
    severity = "minor"
    for issue in issues:
        if issue.get("severity") == "blocker":
            severity = "blocker"
            break
        if issue.get("severity") == "major":
            severity = "major"
    verdict = "request_changes" if severity in ("blocker", "major") else "comment"
    return {"issues": issues, "severity": severity, "verdict": verdict}


def token_ratio(task_tokens: int, review_tokens: int) -> float:
    if task_tokens <= 0:
        return 0.0
    return review_tokens / task_tokens


def record_review(
    connection,
    *,
    pr_id: str,
    round_: int,
    engine_id: str,
    model: str,
    result: dict,
    tokens: int,
    task_tokens: int,
    same_model_fallback: bool = False,
) -> str:
    review_id = new_id()
    connection.execute(
        text("""
        INSERT INTO reviews(id,pr_id,round,engine_id,model,same_model_fallback,
          verdict,issues_json,token_ratio,at)
        VALUES(:id,:pr,:round,:engine,:model,:fallback,:verdict,:issues,:ratio,:at)
    """),
        {
            "id": review_id,
            "pr": pr_id,
            "round": round_,
            "engine": engine_id,
            "model": model,
            "fallback": int(same_model_fallback),
            "verdict": result.get("verdict"),
            "issues": json.dumps(
                {"issues": result.get("issues", []), "severity": result.get("severity")},
                ensure_ascii=False,
            ),
            "ratio": token_ratio(task_tokens, tokens),
            "at": now(),
        },
    )
    return review_id


async def write_review_status(adapter, repo: str, sha: str, result: dict) -> None:
    """pa/ai-review commit status (K6): success only on approve/comment."""
    state = "success" if result["verdict"] in {"approve", "comment"} else "failure"
    await adapter.set_status(
        repo,
        sha,
        "pa/ai-review",
        state,
        description=f"{result.get('severity') or 'clean'} ({result['verdict']})",
    )
