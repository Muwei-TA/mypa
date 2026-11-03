#!/usr/bin/env python3
"""Verify scoped PA identities and live sandbox PR state without any writes.

Run on the Mac after scripts/setup-gitea-sandbox-credentials.py --check.
The two PAT files never leave this process except as HTTPS Authorization headers.
No POST/PATCH/PUT/DELETE requests are issued. No token values are logged.
"""

import asyncio
import json
import os
from pathlib import Path

from pa.config.schema import CodeRepository
from pa.procs.code import CodeExecutor

REPO = "muwei/pa-v11-acceptance-sandbox"
ISSUE = 1
PR_BRANCH = "pa/01M6FQMA5TDA96QQZNY1BZ8B2A"
CI_CONTEXT = "PA v1.1 Sandbox CI / ci (pull_request)"
REVIEW_CONTEXT = "pa/ai-review"
TOKEN_DIR = Path.home() / "Library/Application Support/PA-acceptance-secrets"


async def verify() -> dict:
    # This script is restricted to one disposable repository by design.
    import importlib.util

    helper = Path(__file__).with_name("setup-gitea-sandbox-credentials.py")
    spec = importlib.util.spec_from_file_location("pa_credential_setup", helper)
    if not spec or not spec.loader:
        raise RuntimeError("credential setup module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_saved("agent")
    module.check_saved("merger")

    os.environ["PA_SANDBOX_AGENT_PAT"] = (TOKEN_DIR / "agent.token").read_text().strip()
    os.environ["PA_SANDBOX_MERGER_PAT"] = (TOKEN_DIR / "merger.token").read_text().strip()
    config = CodeRepository(
        scm="gitea",
        remote="ssh://gitea@192.168.50.28:2222/muwei/pa-v11-acceptance-sandbox.git",
        base="main",
        api_url="https://git.muwei.xyz/api/v1",
        agent_token="secret://env/PA_SANDBOX_AGENT_PAT",
        merger_token="secret://env/PA_SANDBOX_MERGER_PAT",
        ci_context=CI_CONTEXT,
        required_checks=[REVIEW_CONTEXT, CI_CONTEXT],
    )
    adapter = CodeExecutor(None, Path("/tmp")).adapter(config)
    issue = await adapter.get_issue(REPO, ISSUE)
    pr = await adapter.find_pr_by_branch(REPO, PR_BRANCH)
    if pr is None or pr.number != 2 or pr.sha is None:
        raise RuntimeError("sandbox acceptance pull request was not found")
    diff = await adapter.get_diff(REPO, pr)
    checks = await adapter.get_checks(REPO, pr)
    merger_view = await adapter.client.request(
        "GET", f"/repos/{REPO}/pulls/{pr.number}", identity="merger"
    )
    identity_separated = (
        adapter.client.identity("agent").token != adapter.client.identity("merger").token
    )
    context_states = {c["context"]: c["state"] for c in checks.contexts}
    checks_passed = all(
        context_states.get(context) == "success" for context in config.required_checks
    )
    if not identity_separated or merger_view.get("number") != pr.number:
        raise RuntimeError("scoped SCM identity verification failed")
    if diff.additions != 1 or diff.deletions != 1 or len(diff.files) != 1:
        raise RuntimeError("unexpected PR diff; stop acceptance")
    return {
        "sandbox": REPO,
        "issue_number": issue.number,
        "pr_number": pr.number,
        "pr_head_sha": pr.sha,
        "pr_state": merger_view.get("state"),
        "draft": merger_view.get("draft"),
        "merged": merger_view.get("merged"),
        "changed_files": len(diff.files),
        "additions": diff.additions,
        "deletions": diff.deletions,
        "checks": {
            context: context_states.get(context, "missing") for context in config.required_checks
        },
        "required_checks_passed": checks_passed,
        "separate_authenticated_agent_and_merger": identity_separated,
        "performed_write": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False, indent=2))
