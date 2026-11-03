"""Code tasks use durable worktrees and platform PRs; model receipts cannot close them.

Only repositories configured by the owner in code.yaml can be used. Local
repositories produce a patch for owner review, never a fictitious platform PR.
Remote PRs wait for configured CI/AI-review contexts and the merge policy.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from pa.ci.merge_gate import merge_gate
from pa.ci.risk import final_risk
from pa.common import PAError, new_id
from pa.notify.cards import create_decision
from pa.scm.base import Diff
from pa.skills.receipt import Artifact
from pa.store.db import transaction
from pa.store.models import Reason
from pa.store.transitions import transition
from pa.workspace.mirror import _git, prepare_workspace


class CodeExecutor:
    def __init__(self, facade, config_dir):
        self.facade = facade
        self.config_dir = config_dir

    def prepare(self, task, config):
        repo = task["repo"]
        entry = config.code.repos.get(repo)
        if entry is None:
            raise PAError("code repository is not configured in code.yaml", "code_setup_failed")
        with self.facade.engine.connect() as c:
            row = (
                c.execute(
                    text("SELECT * FROM code_workspaces WHERE task_id=:id"), {"id": task["id"]}
                )
                .mappings()
                .first()
            )
        if row:
            workspace = Path(row["path"])
        else:
            result = prepare_workspace(
                config.main.data_path(self.config_dir),
                scm=entry.scm,
                repo=repo,
                remote_url=entry.remote,
                task_id=task["id"],
                base_ref=entry.base,
            )
            workspace = Path(result["worktree"])
            with transaction(self.facade.engine) as c:
                c.execute(
                    text(
                        "INSERT INTO code_workspaces(task_id,path,mirror,branch,base_sha) "
                        "VALUES(:id,:path,:mirror,:branch,:sha)"
                    ),
                    {
                        "id": task["id"],
                        "path": str(workspace),
                        "mirror": result["mirror"],
                        "branch": result["branch"],
                        "sha": _git(workspace, "rev-parse", "HEAD").strip(),
                    },
                )
        if _git(workspace, "branch", "--show-current").strip() != f"pa/{task['id']}":
            raise PAError("workspace branch no longer belongs to this task", "code_setup_failed")
        if (workspace / ".codex/config.toml").exists():
            raise PAError(
                "task repositories may not override the PA execution policy", "code_setup_failed"
            )
        return workspace

    def finish_local(self, task, config, receipt):
        entry = config.code.repos[task["repo"]]
        workspace = self.prepare(task, config)
        _git(workspace, "diff", "--check")
        _git(workspace, "add", "--all")
        if _git(workspace, "diff", "--cached", "--name-only").strip():
            _git(
                workspace,
                "-c",
                "user.name=PA Agent",
                "-c",
                "user.email=pa@localhost",
                "commit",
                "-m",
                f"pa: task {task['id']}",
            )
        with self.facade.engine.connect() as c:
            row = (
                c.execute(
                    text("SELECT * FROM code_workspaces WHERE task_id=:id"), {"id": task["id"]}
                )
                .mappings()
                .one()
            )
        base = row["base_sha"]
        patch = _git(workspace, "diff", base, "HEAD")
        head = _git(workspace, "rev-parse", "HEAD").strip()
        names = _git(workspace, "diff", "--numstat", base, "HEAD")
        deleted_paths = set(
            _git(workspace, "diff", "--diff-filter=D", "--name-only", base, "HEAD").splitlines()
        )
        files = []
        for line in names.splitlines():
            added, removed, name = line.split("\t", 2)
            files.append(
                {
                    "path": name,
                    "added": int(added) if added.isdigit() else 200,
                    "removed": int(removed) if removed.isdigit() else 200,
                    "change_type": "removed" if name in deleted_paths else "changed",
                }
            )
        diff = Diff(files, sum(f["added"] for f in files), sum(f["removed"] for f in files))
        from pa.config.schema import SEVEN_TAGS

        receipt.risk = final_risk(
            diff, ai_risk=receipt.risk, tags=receipt.tags, seven_tags=SEVEN_TAGS
        )
        receipt.code_changed = bool(patch)
        artifacts = config.main.data_path(self.config_dir) / "code-artifacts" / task["id"]
        artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        patch_file = artifacts / "changes.patch"
        patch_file.write_text(patch)
        patch_file.chmod(0o600)
        receipt.artifacts.append(Artifact(type="file", ref=str(patch_file)))
        if entry.check_command:
            from pa.sandbox.backends import select_backend
            from pa.sandbox.runtime import review_spec

            backend, _ = select_backend()
            spec = review_spec(entry.check_command, workspace)
            spec.artifacts_dir = artifacts
            checked = backend.run(spec)
            log = artifacts / "checks.txt"
            log.write_text(checked.stdout + checked.stderr)
            log.chmod(0o600)
            if not checked.ok:
                receipt.status = "needs_human"
                receipt.draft = True
                receipt.summary = "代码检查未通过，补丁已保留供处理。"
        with transaction(self.facade.engine) as c:
            c.execute(
                text(
                    "UPDATE code_workspaces SET head_sha=:sha,stage='committed' WHERE task_id=:id"
                ),
                {"sha": head, "id": task["id"]},
            )
        # Even a no-op or failed code task requires a verifiable outcome.
        if entry.scm == "local" or not patch:
            receipt.draft = True
            receipt.status = "needs_human"
            receipt.summary = (
                "本地补丁已生成，等待人工审阅；尚未创建平台 PR。"
                if patch
                else "代码任务没有产生可交付改动。"
            )
        return receipt, workspace, head

    def adapter(self, entry):
        from pa.scm.gitea import GiteaAdapter
        from pa.scm.github import GitHubAdapter
        from pa.scm.http import Client, Identity
        from pa.secrets.resolver import resolve

        if not entry.agent_token:
            raise PAError("SCM agent token is not configured", "code_setup_failed")
        identities = {"agent": Identity("agent", resolve(entry.agent_token))}
        if entry.merger_token:
            identities["merger"] = Identity("merger", resolve(entry.merger_token))
        url = entry.api_url or ("https://api.github.com" if entry.scm == "github" else "")
        if not url:
            raise PAError("Gitea api_url is required", "code_setup_failed")
        client = Client(url, identities)
        return (GitHubAdapter if entry.scm == "github" else GiteaAdapter)(client=client)

    async def finish(self, task, config, receipt):
        receipt, workspace, head = await asyncio.to_thread(self.finish_local, task, config, receipt)
        entry = config.code.repos[task["repo"]]
        if receipt.draft or receipt.status != "succeeded":
            return receipt, False
        adapter = self.adapter(entry)
        try:
            await asyncio.to_thread(
                _git,
                workspace,
                "-c",
                "remote.origin.mirror=false",
                "push",
                "origin",
                f"HEAD:refs/heads/pa/{task['id']}",
            )
            pr = await adapter.find_pr_by_branch(task["repo"], f"pa/{task['id']}")
            if pr is None:
                pr = await adapter.open_pr(
                    task["repo"],
                    f"pa/{task['id']}",
                    entry.base,
                    f"PA: {receipt.summary[:70]}",
                    "本内容由自动化 Agent 生成\n\n" + receipt.summary,
                    [],
                )
            with transaction(self.facade.engine) as c:
                c.execute(
                    text(
                        "INSERT INTO pull_requests(id,task_id,scm,repo,number,url,head,base,"
                        "ci_stage,risk_final) VALUES(:id,:task,:scm,:repo,:number,:url,:head,"
                        ":base,'full_check',:risk) ON CONFLICT(scm,repo,number) DO UPDATE SET "
                        "ci_stage='full_check',risk_final=excluded.risk_final,"
                        "head=excluded.head,base=excluded.base"
                    ),
                    {
                        "id": new_id(),
                        "task": task["id"],
                        "scm": entry.scm,
                        "repo": task["repo"],
                        "number": pr.number,
                        "url": pr.url,
                        "head": pr.head,
                        "base": pr.base,
                        "risk": receipt.risk,
                    },
                )
            with transaction(self.facade.engine) as c:
                from pa.hub.subscriptions import subscribe

                subscribe(
                    c,
                    task_id=task["id"],
                    thread_ref=f"{entry.scm}:{task['repo']}:pulls:{pr.number}",
                )
            if pr.url:
                receipt.artifacts.append(Artifact(type="pr", ref=pr.url))
            return receipt, True
        finally:
            if adapter.client._client:
                await adapter.client._client.aclose()

    def _has_approval(self, connection, task_id, head_sha, *, kind="merge_review"):
        """An old decision must not authorize a new, unreviewed revision."""
        rows = connection.execute(
            text(
                "SELECT reason_json FROM decisions WHERE task_id=:task AND "
                "kind=:kind AND status='answered' AND answer='approve'"
            ),
            {"task": task_id, "kind": kind},
        ).scalars()
        import json

        for value in rows:
            if value and json.loads(value).get("head_sha") == head_sha:
                return True
        return False

    async def _finish_delivery(self, task, config, adapter, recorded):
        """Resume merge -> release request -> verification without re-merging.

        No release hook and no verification means 'merged only', explicitly
        not a claim that a new production version is live.
        """
        import json

        from pa.ci.hooks import release_hook, verify_hook
        from pa.common import now

        entry = config.code.repos[task["repo"]]
        merged_sha = recorded["merged_sha"]
        if not merged_sha:
            # A squash/rebase produces a new commit that is NOT the PR head.
            # Do not trigger a deployment or mark this delivered until Gitea
            # supplies the actual merge commit SHA.
            return {"task_id": task["id"], "status": "awaiting_ci", "delivery": "merge_sha_unknown"}
        stage = recorded["ci_stage"]
        if stage == "merged":
            if entry.release_hook != "none":
                with transaction(self.facade.engine) as c:
                    current = (
                        c.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                        .mappings()
                        .one()
                    )
                    if current["status"] != "awaiting_ci":
                        return None
                    release_auto = recorded["merge_action"] in {
                        "auto_release",
                        "confirm_merge_and_release",
                    }
                    approved = self._has_approval(c, task["id"], merged_sha, kind="release_review")
                    if not release_auto and not approved:
                        transition(
                            c,
                            task["id"],
                            "awaiting_decision",
                            current["version"],
                            [Reason(code="release_requires_owner")],
                        )
                        create_decision(
                            c,
                            task_id=task["id"],
                            kind="release_review",
                            question="PR 已合并，是否触发此版本的发布流程？",
                            recommend="approve",
                            options=["approve", "take_over"],
                            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                            reason_json=json.dumps({"head_sha": merged_sha}),
                        )
                        return {
                            "task_id": task["id"],
                            "status": "awaiting_decision",
                            "delivery": "release_approval",
                        }
                await release_hook(
                    entry.release_hook,
                    adapter=adapter,
                    repo=task["repo"],
                    sha=merged_sha,
                    workflow=entry.release_workflow,
                    ref=entry.base,
                )
                with transaction(self.facade.engine) as c:
                    c.execute(
                        text(
                            "UPDATE pull_requests SET ci_stage='released' "
                            "WHERE id=:id AND ci_stage='merged'"
                        ),
                        {"id": recorded["id"]},
                    )
                stage = "released"
        verified = False
        if stage in ("merged", "released") and entry.verify_hook != "none":
            mirror = None
            if entry.verify_hook == "version_endpoint":
                from pa.workspace.mirror import ensure_mirror

                # The mirror must contain the actual merge commit and the
                # deployed live ref. Refresh it on each polling attempt.
                mirror = await asyncio.to_thread(
                    ensure_mirror,
                    config.main.data_path(self.config_dir),
                    entry.scm,
                    task["repo"],
                    entry.remote,
                )
            outcome = await verify_hook(
                entry.verify_hook,
                args={
                    "url": entry.verify_url,
                    "status": entry.verify_status,
                    "keyword": entry.verify_keyword,
                },
                mirror=mirror,
                merged_sha=merged_sha,
                attempts=1,  # polling worker retries instead of blocking 10 minutes
            )
            if not outcome["verified"]:
                return {
                    "task_id": task["id"],
                    "status": "awaiting_ci",
                    "delivery": "verification_pending",
                }
            verified = True
            with transaction(self.facade.engine) as c:
                c.execute(
                    text(
                        "UPDATE pull_requests SET ci_stage='verified',verified_at=:at,"
                        "live_sha=:live WHERE id=:id"
                    ),
                    {"at": now(), "id": recorded["id"], "live": outcome.get("live_sha")},
                )
        elif stage == "verified":
            verified = True
        with transaction(self.facade.engine) as c:
            current = (
                c.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                .mappings()
                .one()
            )
            if current["status"] != "awaiting_ci":
                return None
            for status in ("succeeded", "judged", "closed"):
                current = transition(
                    c,
                    task["id"],
                    status,
                    current["version"],
                    [
                        Reason(
                            code="live_version_verified"
                            if verified and entry.verify_hook == "version_endpoint"
                            else "release_http_verified"
                            if verified
                            else "pr_merged_no_verification"
                        )
                    ],
                )
        return {"task_id": task["id"], "status": "closed", "release_verified": verified}

    def _on_check_failure(self, task, config, pr, checks):
        """A failed required check returns the same worktree to the Agent.

        The first two failed rounds retry automatically. The third requires
        the owner's decision. Pending/missing checks never trigger retries.
        """
        import json

        with transaction(self.facade.engine) as c:
            current = (
                c.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                .mappings()
                .one()
            )
            if current["status"] != "awaiting_ci":
                return None
            attempt = current["fix_round"] + 1
            at_limit = attempt >= config.main.limits.max_fix_rounds
            c.execute(
                text("UPDATE tasks SET fix_round=:round WHERE id=:id"),
                {"id": task["id"], "round": attempt},
            )
            reason = [
                Reason(
                    code="ci_check_failed",
                    values={
                        "sha": pr.sha,
                        "round": attempt,
                        "failed_checks": [
                            name
                            for name in checks.required
                            if any(
                                item["context"] == name and item["state"] in ("failure", "error")
                                for item in checks.contexts
                            )
                        ],
                    },
                )
            ]
            updated = transition(
                c,
                task["id"],
                "awaiting_decision" if at_limit else "queued",
                current["version"],
                reason,
            )
            c.execute(
                text("UPDATE pull_requests SET ci_stage=:stage WHERE task_id=:id"),
                {"stage": "needs_owner" if at_limit else "fix_requested", "id": task["id"]},
            )
            if at_limit:
                create_decision(
                    c,
                    task_id=task["id"],
                    kind="code_fix_limit",
                    question="代码检查连续失败，是否再给一次修复机会？",
                    recommend="take_over",
                    options=["one_more_round", "take_over"],
                    expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                    reason_json=json.dumps({"head_sha": pr.sha, "fix_round": attempt}),
                )
        return {"task_id": task["id"], "status": updated["status"], "fix_round": attempt}

    async def poll(self, task, config):
        import json

        entry = config.code.repos[task["repo"]]
        adapter = self.adapter(entry)
        try:
            with self.facade.engine.connect() as c:
                recorded = (
                    c.execute(
                        text(
                            "SELECT * FROM pull_requests WHERE task_id=:id "
                            "ORDER BY rowid DESC LIMIT 1"
                        ),
                        {"id": task["id"]},
                    )
                    .mappings()
                    .first()
                )
                head = c.execute(
                    text("SELECT head_sha FROM code_workspaces WHERE task_id=:id"),
                    {"id": task["id"]},
                ).scalar_one_or_none()
            if recorded is None or not head:
                return None
            if recorded["ci_stage"] in {"merged", "released", "verified"}:
                return await self._finish_delivery(task, config, adapter, recorded)
            pr = await adapter.find_pr_by_branch(task["repo"], f"pa/{task['id']}")
            if pr is None or pr.state != "open" or not pr.sha or pr.sha != head:
                return None  # Missing/stale PR cannot be treated as delivered.
            checks = await adapter.get_checks(task["repo"], pr)
            checks.required = entry.required_checks
            latest = {item["context"]: item["state"] for item in checks.contexts}
            if any(latest.get(name) in ("failure", "error") for name in checks.required):
                return self._on_check_failure(task, config, pr, checks)
            if not checks.all_required_passed:
                return None
            verdict = merge_gate(
                config.merge_policy, repo=task["repo"], risk=recorded["risk_final"], checks=checks
            )
            if (
                verdict["action"] == "blocked"
                and verdict["reason"] == "merge policy is not configured"
            ):
                return None  # Never authorize a merge without a policy.
            with transaction(self.facade.engine) as c:
                current = (
                    c.execute(
                        text("SELECT * FROM tasks WHERE id=:id"),
                        {"id": task["id"]},
                    )
                    .mappings()
                    .one()
                )
                if current["status"] != "awaiting_ci":
                    return None
                approved = self._has_approval(c, task["id"], head)
                auto = (
                    verdict["action"] in {"auto_release", "auto_merge_notify"}
                    and recorded["risk_final"] != "high"
                )
                if not entry.merger_token or (not auto and not approved):
                    transition(
                        c,
                        task["id"],
                        "awaiting_decision",
                        current["version"],
                        [Reason(code="merge_requires_owner", values={"sha": head})],
                    )
                    can_merge = bool(entry.merger_token)
                    create_decision(
                        c,
                        task_id=task["id"],
                        kind="merge_review" if can_merge else "merge_handoff",
                        question=(
                            "检查通过：是否批准合并此版本的 PR？"
                            if can_merge
                            else "缺少独立合并身份，请在平台接手此 PR。"
                        ),
                        recommend="approve" if can_merge else "take_over",
                        options=["approve", "take_over", "retry"]
                        if can_merge
                        else ["take_over", "retry"],
                        expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                        reason_json=json.dumps({"head_sha": head, "pr_number": pr.number}),
                    )
                    return {"task_id": task["id"], "status": "awaiting_decision"}
            # Approval is tied to this immutable head; platform protections
            # must still refuse stale or failing checks at the merge endpoint.
            merged = await adapter.merge(task["repo"], pr, "squash")
            if not merged.merged:
                return None
            with transaction(self.facade.engine) as c:
                current = (
                    c.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                    .mappings()
                    .one()
                )
                if current["status"] != "awaiting_ci":
                    return None
                c.execute(
                    text(
                        "UPDATE pull_requests SET ci_stage='merged',merged_sha=:sha,"
                        "merge_action=:action WHERE id=:id"
                    ),
                    {"sha": merged.sha, "action": verdict["action"], "id": recorded["id"]},
                )
            refreshed = {
                **recorded,
                "ci_stage": "merged",
                "merged_sha": merged.sha,
                "merge_action": verdict["action"],
            }
            return await self._finish_delivery(task, config, adapter, refreshed)
        finally:
            if adapter.client._client:
                await adapter.client._client.aclose()
