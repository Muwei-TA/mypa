"""Contract tests for the SCM adapters (M2-01/M2-02): one suite, both platforms."""

import asyncio
import hashlib
import hmac as hmac_mod

import pytest

from pa.scm.base import PR, CheckSummary, Diff, Issue, ScmAdapter
from pa.scm.gitea import GiteaAdapter
from pa.scm.github import GitHubAdapter
from pa.scm.http import Identity, ScmError


class FakeTransport:
    """Records requests; replies from a scripted route table."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    async def __call__(self, method, path, *, identity="agent", json_body=None, params=None):
        self.requests.append(
            {
                "method": method,
                "url": path,
                "identity": identity,
                "json": json_body,
                "params": params,
            }
        )
        for route in self.routes:
            if route["method"] != method:
                continue
            if route["url"] == path or (
                route["url"].endswith("?") and path.startswith(route["url"][:-1])
            ):
                if route.get("status", 200) >= 400:
                    raise ScmError(
                        f"{method} {path} -> {route['status']}",
                        route.get("kind", "permanent"),
                        route["status"],
                    )
                return route.get("response", {})
        return {}


def _signed(secret, body):
    return "sha256=" + hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _client(routes, identities):
    from pa.scm.http import Client

    client = Client("https://api.example.test", identities)
    client.request = FakeTransport(routes)
    return client, client.request


@pytest.fixture
def identities():
    return {
        "agent": Identity("agent", "tok-agent"),
        "merger": Identity("merger", "tok-merger"),
    }


@pytest.fixture
def github(identities):
    client, transport = _client(
        [
            {
                "method": "GET",
                "url": "/repos/o/r/issues/12",
                "response": {
                    "number": 12,
                    "title": "Fix login",
                    "body": "desc",
                    "user": {"login": "owner"},
                    "labels": [{"name": "pa:fix"}],
                    "html_url": "https://example.test/12",
                },
            },
            {
                "method": "POST",
                "url": "/repos/o/r/pulls",
                "response": {
                    "number": 7,
                    "head": {"ref": "pa/t1", "sha": "abc123"},
                    "base": {"ref": "main"},
                    "title": "Fix",
                    "state": "open",
                    "html_url": "https://example.test/pr7",
                },
            },
            {
                "method": "GET",
                "url": "/repos/o/r/pulls/7/files",
                "response": [
                    {"filename": "src/a.py", "additions": 10, "deletions": 2, "patch": "@@"}
                ],
            },
            {
                "method": "GET",
                "url": "/repos/o/r/commits/abc123/check-runs",
                "response": {
                    "total_count": 1,
                    "check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}],
                },
            },
            {
                "method": "PUT",
                "url": "/repos/o/r/pulls/7/merge",
                "response": {"merged": True, "sha": "mergedsha"},
            },
            {"method": "POST", "url": "/repos/o/r/statuses/abc123", "response": {}},
            {
                "method": "GET",
                "url": "/repos/o/r/pulls?",
                "response": [
                    {
                        "number": 7,
                        "head": {"ref": "pa/t1", "sha": "abc123"},
                        "base": {"ref": "main"},
                        "title": "Fix",
                        "state": "open",
                        "html_url": "https://example.test/pr7",
                    }
                ],
            },
        ],
        identities,
    )
    return GitHubAdapter(client=client, webhook_secret="s3cr3t"), transport


@pytest.fixture
def gitea(identities):
    routes = [
        {
            "method": "GET",
            "url": "/repos/o/r/issues/12",
            "response": {
                "number": 12,
                "title": "Fix login",
                "body": "desc",
                "user": {"login": "owner"},
                "labels": [{"name": "pa:fix"}],
                "html_url": "https://example.test/12",
            },
        },
        {
            "method": "POST",
            "url": "/repos/o/r/pulls",
            "response": {
                "number": 7,
                "head": {"sha": "abc123"},
                "base": {"ref": "main"},
                "title": "Fix",
                "state": "open",
                "html_url": "https://example.test/pr7",
            },
        },
        {
            "method": "GET",
            "url": "/repos/o/r/pulls/7/diff",
            "response": {"files": [{"filename": "srca.py", "additions": 10, "deletions": 2}]},
        },
        {
            "method": "GET",
            "url": "/repos/o/r/statuses/abc123",
            "response": [{"context": "ci", "status": "success"}],
        },
        {
            "method": "POST",
            "url": "/repos/o/r/pulls/7/merge",
            "response": {"merged": True},
        },
        {"method": "POST", "url": "/repos/o/r/statuses/abc123", "response": {}},
        {
            "method": "GET",
            "url": "/repos/o/r/pulls",
            "response": [
                {
                    "number": 7,
                    "head": {"ref": "pa/t1", "sha": "abc123"},
                    "base": {"ref": "main"},
                    "title": "Fix",
                    "state": "open",
                    "html_url": "https://example.test/pr7",
                }
            ],
        },
    ]
    client, transport = _client(routes, identities)
    return GiteaAdapter(client=client, webhook_secret="s3cr3t"), transport


CASES = ["github", "gitea"]


@pytest.mark.parametrize("platform", CASES)
def test_contract_ten_methods(platform, github, gitea, request):
    adapter, _ = request.getfixturevalue(platform)
    assert isinstance(adapter, ScmAdapter)
    for name in (
        "get_issue",
        "open_pr",
        "comment",
        "get_diff",
        "get_checks",
        "merge",
        "dispatch_workflow",
        "set_status",
        "find_pr_by_branch",
        "verify_webhook",
    ):
        assert hasattr(adapter, name)


@pytest.mark.parametrize("platform", CASES)
def test_get_issue_roundtrip(platform, github, gitea, request):
    adapter, _ = request.getfixturevalue(platform)
    issue = asyncio.run(adapter.get_issue("o/r", 12))
    assert isinstance(issue, Issue)
    assert issue.number == 12 and issue.labels == ["pa:fix"]


@pytest.mark.parametrize("platform", CASES)
def test_open_pr_and_diff(platform, github, gitea, request):
    adapter, _ = request.getfixturevalue(platform)
    pr = asyncio.run(adapter.open_pr("o/r", "pa/t1", "main", "Fix", "body", []))
    assert isinstance(pr, PR) and pr.number == 7 and pr.head == "pa/t1"
    diff = asyncio.run(adapter.get_diff("o/r", pr))
    assert isinstance(diff, Diff) and diff.changed_lines == 12
    checks = asyncio.run(adapter.get_checks("o/r", pr))
    assert isinstance(checks, CheckSummary) and checks.state == "success"


@pytest.mark.parametrize("platform", CASES)
def test_merge_uses_merger_identity(platform, github, gitea, request):
    adapter, transport = request.getfixturevalue(platform)
    pr = asyncio.run(adapter.find_pr_by_branch("o/r", "pa/t1"))
    assert pr is not None and pr.number == 7
    result = asyncio.run(adapter.merge("o/r", pr, "squash"))
    assert result.merged and result.method == "squash"
    merge_calls = [r for r in transport.requests if "merge" in r["url"]]
    assert merge_calls[0]["identity"] == "merger"


@pytest.mark.parametrize("platform", CASES)
def test_status_and_comment_identities(platform, github, gitea, request):
    adapter, transport = request.getfixturevalue(platform)
    asyncio.run(adapter.set_status("o/r", "abc123", "pa/ai-review", "success"))
    status_calls = [r for r in transport.requests if "/statuses/" in r["url"]]
    assert status_calls and status_calls[0]["identity"] == "agent"
    asyncio.run(adapter.comment("o/r", 12, "hello"))
    comment_calls = [r for r in transport.requests if "/comments" in r["url"]]
    assert comment_calls[0]["identity"] == "agent"


@pytest.mark.parametrize("platform", CASES)
def test_webhook_hmac_verification(platform, github, gitea, request):
    adapter, _ = request.getfixturevalue(platform)
    body = b'{"action":"opened"}'
    good = _signed("s3cr3t", body)
    assert adapter.verify_webhook({"X-Hub-Signature-256": good}, body) or adapter.verify_webhook(
        {"X-Gitea-Signature": good}, body
    )
    assert not adapter.verify_webhook({"X-Hub-Signature-256": "sha256=bad"}, body)


def test_gitea_ip_allowlist(identities):
    client, _ = _client([], identities)
    adapter = GiteaAdapter(client=client, webhook_secret="s", allowed_ips=["192.168.1.10"])
    body = b"{}"
    signature = _signed("s", body)
    assert not adapter.verify_webhook(
        {"X-Gitea-Signature": signature, "X-Forwarded-For": "8.8.8.8"}, body
    )
    assert adapter.verify_webhook(
        {"X-Gitea-Signature": signature, "X-Forwarded-For": "192.168.1.10"}, body
    )


def test_client_error_classification(identities):
    client = _client(
        [
            {
                "method": "GET",
                "url": "/repos/o/r/issues/12",
                "status": 500,
                "kind": "transient",
            }
        ],
        identities,
    )[0]
    with pytest.raises(ScmError) as exc:
        asyncio.run(client.request("GET", "/repos/o/r/issues/12"))
    assert exc.value.kind == "transient"


def test_missing_identity_raises():
    from pa.scm.http import Client

    client = Client("https://api.example.test", {"agent": Identity("agent", "t")})
    with pytest.raises(ScmError) as exc:
        client.identity("merger")
    assert exc.value.kind == "auth"
