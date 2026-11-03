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
        files = []
        for line in names.splitlines():
            added, removed, name = line.split("\t", 2)
            files.append(
                {
                    "path": name,
                    "added": int(added) if added.isdigit() else 200,
                    "removed": int(removed) if removed.isdigit() else 200,
                    "change_type": "changed",
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
                        "ci_stage='full_check',risk_final=excluded.risk_final"
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
            if pr.url:
                receipt.artifacts.append(Artifact(type="pr", ref=pr.url))
            return receipt, True
        finally:
            if adapter.client._client:
                await adapter.client._client.aclose()

    async def poll(self, task, config):
        entry = config.code.repos[task["repo"]]
        adapter = self.adapter(entry)
        try:
            pr = await adapter.find_pr_by_branch(task["repo"], f"pa/{task['id']}")
            if pr is None:
                return None  # Never infer delivery from a missing/open-PR listing.
            checks = await adapter.get_checks(task["repo"], pr)
            checks.required = entry.required_checks
            if not checks.all_required_passed:
                return None
            with self.facade.engine.connect() as c:
                recorded = (
                    c.execute(
                        text("SELECT * FROM pull_requests WHERE task_id=:id"), {"id": task["id"]}
                    )
                    .mappings()
                    .first()
                )
                head = c.execute(
                    text("SELECT head_sha FROM code_workspaces WHERE task_id=:id"),
                    {"id": task["id"]},
                ).scalar_one()
            if recorded is None or pr.sha != head:
                return None
            verdict = merge_gate(
                config.merge_policy, repo=task["repo"], risk=recorded["risk_final"], checks=checks
            )
            # Releases remain an explicit owner action. Only the policy's
            # auto_merge_notify cell is an implemented automatic delivery here.
            with transaction(self.facade.engine) as c:
                current = (
                    c.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                    .mappings()
                    .one()
                )
                if current["status"] != "awaiting_ci":
                    return None
                if verdict["action"] != "auto_merge_notify" or not entry.merger_token:
                    transition(
                        c,
                        task["id"],
                        "awaiting_decision",
                        current["version"],
                        [Reason(code="merge_requires_owner")],
                    )
                    create_decision(
                        c,
                        task_id=task["id"],
                        kind="merge_review",
                        question="PR 检查已通过，请审阅并在平台完成合并或接手。",
                        recommend="take_over",
                        options=["take_over", "retry"],
                        expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                    )
                    return {"task_id": task["id"], "status": "awaiting_decision"}
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
                for stage in ("succeeded", "judged", "closed"):
                    current = transition(
                        c,
                        task["id"],
                        stage,
                        current["version"],
                        [Reason(code="pr_merged_no_release")],
                    )
                c.execute(
                    text("UPDATE pull_requests SET ci_stage='merged',merged_sha=:sha WHERE id=:id"),
                    {"sha": merged.sha, "id": recorded["id"]},
                )
            return {"task_id": task["id"], "status": "closed"}
        finally:
            if adapter.client._client:
                await adapter.client._client.aclose()
