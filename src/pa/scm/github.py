"""GitHub adapter over the ScmAdapter contract (PRD §10.1).

All write operations carry identity=agent except merge (merger identity, v1.1·D17).
The HTTP layer keeps no secrets in logs; tokens stay in headers only.
"""

from typing import Literal

from pa.scm.base import (
    PR,
    CheckSummary,
    Diff,
    Issue,
    MergeResult,
    RunRef,
    ScmAdapter,
)
from pa.scm.http import Client, verify_hmac


class GitHubAdapter(ScmAdapter):
    platform = "github"

    def __init__(
        self,
        api_url: str = "https://api.github.com",
        client: Client | None = None,
        *,
        webhook_secret: str = "",
    ):
        self.client = client or Client(api_url)
        self.webhook_secret = webhook_secret

    async def get_issue(self, repo: str, number: int) -> Issue:
        data = await self.client.request("GET", f"/repos/{repo}/issues/{number}")
        return Issue(
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            author=data["user"]["login"],
            labels=[label["name"] for label in data.get("labels", [])],
            url=data.get("html_url"),
        )

    async def open_pr(
        self, repo: str, head: str, base: str, title: str, body: str, labels: list[str]
    ) -> PR:
        data = await self.client.request(
            "POST",
            f"/repos/{repo}/pulls",
            json_body={"head": head, "base": base, "title": title, "body": body},
        )
        pr = PR(
            number=data["number"],
            head=data["head"]["ref"],
            base=data["base"]["ref"],
            title=data["title"],
            body=body,
            state=data.get("state", "open"),
            sha=data["head"]["sha"],
            url=data.get("html_url"),
        )
        for label in labels:
            await self.client.request(
                "POST",
                f"/repos/{repo}/issues/{data['number']}/labels",
                json_body={"labels": [label]},
            )
        return pr

    async def comment(self, repo: str, number: int, body: str) -> None:
        await self.client.request(
            "POST", f"/repos/{repo}/issues/{number}/comments", json_body={"body": body}
        )

    async def get_diff(self, repo: str, pr: PR) -> Diff:
        files = await self.client.request("GET", f"/repos/{repo}/pulls/{pr.number}/files")
        return Diff(
            files=[
                {
                    "path": f["filename"],
                    "added": f.get("additions", 0),
                    "removed": f.get("deletions", 0),
                    "change_type": f.get("status", "changed"),
                    "patch": f.get("patch", ""),
                }
                for f in files
            ],
            additions=sum(f.get("additions", 0) for f in files),
            deletions=sum(f.get("deletions", 0) for f in files),
        )

    async def get_checks(self, repo: str, pr: PR) -> CheckSummary:
        # GitHub Checks API: any non-success completed run means failure.
        data = await self.client.request("GET", f"/repos/{repo}/commits/{pr.sha}/check-runs")
        statuses = await self.client.request("GET", f"/repos/{repo}/commits/{pr.sha}/status")
        contexts = [
            {
                "context": run["name"],
                "state": (
                    "success"
                    if run["status"] == "completed" and run["conclusion"] == "success"
                    else "failure"
                    if run["status"] == "completed"
                    else "pending"
                ),
                "url": run.get("details_url"),
            }
            for run in data.get("check_runs", [])
        ]
        contexts += [
            {"context": s["context"], "state": s["state"], "url": s.get("target_url")}
            for s in statuses.get("statuses", [])
        ]
        # The platform returns newest statuses first. Historical failures must
        # not override a newer success, or vice versa.
        contexts = list({c["context"]: c for c in reversed(contexts)}.values())
        state = (
            "success"
            if contexts and all(c["state"] == "success" for c in contexts)
            else "failure"
            if any(c["state"] == "failure" for c in contexts)
            else "pending"
            if contexts
            else "unknown"
        )
        return CheckSummary(state=state, contexts=contexts)

    async def merge(
        self, repo: str, pr: PR, method: Literal["squash", "merge", "rebase"]
    ) -> MergeResult:
        # Merger identity only (v1.1·D17): agent tokens never merge.
        data = await self.client.request(
            "PUT",
            f"/repos/{repo}/pulls/{pr.number}/merge",
            identity="merger",
            json_body={"merge_method": method, "sha": pr.sha},
        )
        return MergeResult(
            merged=bool(data.get("merged", False)), sha=data.get("sha"), method=method
        )

    async def dispatch_workflow(
        self, repo: str, workflow: str, ref: str, inputs: dict[str, str]
    ) -> RunRef:
        await self.client.request(
            "POST",
            f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
            json_body={"ref": ref, "inputs": inputs},
        )
        return RunRef(id=f"{repo}:{workflow}:{ref}", url=None)

    async def set_status(
        self,
        repo: str,
        sha: str,
        context: str,
        state: str,
        description: str = "",
        url: str | None = None,
    ) -> None:
        await self.client.request(
            "POST",
            f"/repos/{repo}/statuses/{sha}",
            json_body={
                "state": state,
                "context": context,
                "description": description,
                "target_url": url,
            },
        )

    async def find_pr_by_branch(self, repo: str, branch: str) -> PR | None:
        data = await self.client.request(
            "GET",
            f"/repos/{repo}/pulls",
            params={"head": f"{repo.split('/')[0]}:{branch}", "state": "open"},
        )
        for item in data:
            if item["head"]["ref"] == branch:
                return PR(
                    number=item["number"],
                    head=item["head"]["ref"],
                    base=item["base"]["ref"],
                    title=item["title"],
                    body=item.get("body") or "",
                    state=item.get("state", "open"),
                    sha=item["head"]["sha"],
                    url=item.get("html_url"),
                )
        return None

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return verify_hmac(self.webhook_secret, body, headers.get("X-Hub-Signature-256"))
