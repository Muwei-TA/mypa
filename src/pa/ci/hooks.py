"""Release and verification hooks (M2-13, PRD §10.2 / v1.1·K7).

release hooks: none | tag | workflow_dispatch | script
verify hooks: none | http_check | version_endpoint | script

version_endpoint uses git merge-base --is-ancestor <merged_sha> <live_sha>
in the mirror; retries every 30 seconds for up to 10 minutes. The result is
written back as the pa/verify commit status; failures go to the owner.
"""

import re
import subprocess
from pathlib import Path

from pa.common import PAError


async def release_hook(
    kind: str,
    *,
    adapter=None,
    repo: str = "",
    sha: str = "",
    workflow: str = "",
    ref: str = "",
    script: str = "",
) -> dict:
    if kind == "none":
        return {"released": True, "hook": "none"}
    if kind == "tag":
        return {"released": True, "hook": "tag", "tag": f"pa-release-{sha[:8]}"}
    if kind == "workflow_dispatch":
        if adapter is None:
            raise PAError("workflow_dispatch requires a platform adapter")
        run = await adapter.dispatch_workflow(repo, workflow, ref, {})
        return {"released": True, "hook": "workflow_dispatch", "run": run.id}
    if kind == "script":
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=300, check=False
        )
        if result.returncode != 0:
            raise PAError(f"release script failed: {result.stderr[:200]}")
        return {"released": True, "hook": "script"}
    raise PAError(f"unknown release hook: {kind}")


async def verify_http(kind_args: dict) -> bool:
    import httpx

    url = kind_args.get("url", "")
    expect_status = kind_args.get("status", 200)
    keyword = kind_args.get("keyword", "")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
    if response.status_code != expect_status:
        return False
    return not keyword or keyword in response.text


def version_contains(mirror: Path, merged_sha: str, live_sha: str) -> bool:
    """git merge-base --is-ancestor <merged> <live> (K7)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", merged_sha, live_sha],
        cwd=mirror,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


async def fetch_live_sha(url: str) -> str | None:
    """Read the deployed Git SHA, never trust HTTP 200 alone as deployment proof.

    Accepted responses: JSON object with `sha`, `commit`, or `git_sha` (40 or
    64 lowercase/uppercase hex characters). A malformed response fails closed.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = next((data[k] for k in ("sha", "commit", "git_sha") if k in data), None)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        return None
    return value.lower()


async def verify_hook(
    kind: str,
    *,
    args: dict | None = None,
    mirror: Path | None = None,
    merged_sha: str = "",
    live_sha: str = "",
    attempts: int = 20,
    interval_s: float = 30.0,
) -> dict:
    """Verify the live deployment includes the merge; retry for 10 minutes."""
    args = args or {}
    if kind == "none":
        return {"verified": True, "hook": "none"}
    if kind == "http_check":
        ok = await verify_http(args)
        return {"verified": ok, "hook": "http_check"}
    if kind == "version_endpoint":
        if mirror is None:
            raise PAError("version_endpoint requires the mirror path")
        import asyncio

        for index in range(max(1, attempts)):
            current_sha = await fetch_live_sha(args["url"]) if args.get("url") else live_sha
            if current_sha and version_contains(mirror, merged_sha, current_sha):
                return {"verified": True, "hook": "version_endpoint", "live_sha": current_sha}
            if index + 1 < max(1, attempts):
                await asyncio.sleep(interval_s)
        return {"verified": False, "hook": "version_endpoint", "live_sha": None}
    if kind == "script":
        result = subprocess.run(
            ["bash", "-c", args.get("script", "")],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return {"verified": result.returncode == 0, "hook": "script"}
    raise PAError(f"unknown verify hook: {kind}")


async def write_verify_status(adapter, repo: str, sha: str, verified: bool) -> None:
    await adapter.set_status(
        repo,
        sha,
        "pa/verify",
        "success" if verified else "failure",
        description="live version verified" if verified else "live version check failed",
    )
