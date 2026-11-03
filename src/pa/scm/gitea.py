"""Gitea adapter: same contract as GitHub (PRD §10.1, task M2-02).

Commit statuses post to /statuses/{sha}; token auth via the shared Client.
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


class GiteaAdapter(ScmAdapter):
    platform = "gitea"

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:3000",
        client: Client | None = None,
        *,
        webhook_secret: str = "",
        allowed_ips: list[str] | None = None,
    ):
        self.client = client or Client(api_url)
        self.webhook_secret = webhook_secret
        self.allowed_ips = allowed_ips or []

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
            json_body={
                "head": head,
                "base": base,
                "title": title,
                "body": body,
            },
        )
        pr = PR(
            number=data["number"],
            head=head,
            base=base,
            title=title,
            body=body,
            state=data.get("state", "open"),
            sha=data.get("head", {}).get("sha"),
            url=data.get("html_url"),
        )
        if labels:
            await self.client.request(
                "POST",
                f"/repos/{repo}/issues/{data['number']}/labels",
                json_body={"labels": labels},
            )
        return pr

    async def comment(self, repo: str, number: int, body: str) -> None:
        await self.client.request(
            "POST", f"/repos/{repo}/issues/{number}/comments", json_body={"body": body}
        )

    async def get_diff(self, repo: str, pr: PR) -> Diff:
        data = await self.client.request("GET", f"/repos/{repo}/pulls/{pr.number}/diff")
        files = []
        for f in data.get("files", []):
            files.append(
                {
                    "path": f["filename"],
                    "added": f.get("additions", 0),
                    "removed": f.get("deletions", 0),
                    "change_type": "changed",
                    "patch": f.get("patch", ""),
                }
            )
        return Diff(
            files=files,
            additions=sum(f["added"] for f in files),
            deletions=sum(f["removed"] for f in files),
        )

    async def get_checks(self, repo: str, pr: PR) -> CheckSummary:
        # Gitea commit statuses for the head sha.
        data = await self.client.request("GET", f"/repos/{repo}/statuses/{pr.sha}")
        # /statuses/{sha} includes historical attempts. Gitea normally
        # returns newest first; the newest result for each context wins.
        # Otherwise an old failure would permanently block a repaired PR.
        latest = {}
        for item in data:
            name = item.get("context", "")
            if name and name not in latest:
                latest[name] = {
                    "context": name,
                    "state": item.get("status", "unknown"),
                    "url": item.get("target_url"),
                }
        contexts = list(latest.values())
        state = (
            "success"
            if contexts and all(c["state"] == "success" for c in contexts)
            else "failure"
            if any(c["state"] in ("failure", "error") for c in contexts)
            else "pending"
            if contexts
            else "unknown"
        )
        return CheckSummary(state=state, contexts=contexts)

    async def merge(
        self, repo: str, pr: PR, method: Literal["squash", "merge", "rebase"]
    ) -> MergeResult:
        data = await self.client.request(
            "POST",
            f"/repos/{repo}/pulls/{pr.number}/merge",
            identity="merger",
            json_body={"Do": method.title(), "merge_title_field": pr.title},
        )
        return MergeResult(
            merged=bool(data.get("merged", True)), sha=data.get("sha"), method=method
        )

    async def dispatch_workflow(
        self, repo: str, workflow: str, ref: str, inputs: dict[str, str]
    ) -> RunRef:
        await self.client.request(
            "POST",
            f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
            json_body={"ref": ref, "inputs": inputs},
        )
        return RunRef(id=f"{repo}:{workflow}:{ref}")

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

    async def find_pr_by_branch(self, repo: str, head: str) -> PR | None:
        data = await self.client.request("GET", f"/repos/{repo}/pulls", params={"state": "open"})
        for item in data:
            if item["head"]["ref"] == head:
                return PR(
                    number=item["number"],
                    head=item["head"]["ref"],
                    base=item["base"]["ref"],
                    title=item["title"],
                    body=item.get("body") or "",
                    state=item.get("state", "open"),
                    sha=item["head"].get("sha"),
                    url=item.get("html_url"),
                )
        return None

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        if self.allowed_ips:
            client_ip = headers.get("X-Forwarded-For", headers.get("X-Real-IP", ""))
            if not any(
                client_ip == ip or client_ip.startswith(ip.rstrip("*")) for ip in self.allowed_ips
            ):
                return False
        native = headers.get("X-Gitea-Signature", "")
        return bool(
            (
                native
                and (
                    verify_hmac(self.webhook_secret, body, "sha256=" + native)
                    or verify_hmac(self.webhook_secret, body, native)
                )
            )
            or verify_hmac(self.webhook_secret, body, headers.get("X-Hub-Signature-256"))
        )
