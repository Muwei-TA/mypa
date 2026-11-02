"""Webhook ingestion tests (M2-03): signature, throttle, idempotency, parsing."""

import hashlib
import hmac as hmac_mod
import json

from fastapi.testclient import TestClient

from pa.api.app import create_app
from pa.ingress.github import GiteaWebhookAdapter, GitHubWebhookAdapter
from pa.ingress.webhook_common import WebhookGuard


def _signed(secret, body: bytes) -> str:
    return "sha256=" + hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_guard_accepts_valid_signature():
    guard = WebhookGuard(secret="s")
    body = b'{"hello": 1}'
    ok, reason = guard.check(
        ip="1.2.3.4", headers={"X-Hub-Signature-256": _signed("s", body)}, body=body
    )
    assert ok and reason == "ok"


def test_guard_rejects_bad_signature_and_counts_failures():
    guard = WebhookGuard(secret="s", max_failures=3)
    body = b"{}"
    for _ in range(3):
        ok, reason = guard.check(
            ip="9.9.9.9", headers={"X-Hub-Signature-256": "sha256=bad"}, body=body
        )
        assert not ok and reason == "bad signature"
    # Fourth attempt: blacklisted even with a valid signature.
    ok, reason = guard.check(
        ip="9.9.9.9", headers={"X-Hub-Signature-256": _signed("s", body)}, body=body
    )
    assert not ok and reason == "blacklisted"
    assert guard.blacklisted("9.9.9.9")
    # Other IPs unaffected.
    ok, _ = guard.check(
        ip="1.1.1.1", headers={"X-Hub-Signature-256": _signed("s", body)}, body=body
    )
    assert ok


def test_guard_rejects_stale_timestamp():
    guard = WebhookGuard(secret="s", max_age_s=300)
    body = b"{}"
    ok, reason = guard.check(
        ip="2.2.2.2",
        headers={"X-Hub-Signature-256": _signed("s", body), "X-Gitea-Timestamp": "1000000000"},
        body=body,
    )
    assert not ok and reason == "stale timestamp"


ISSUE_PAYLOAD = {
    "action": "opened",
    "issue": {
        "number": 12,
        "title": "Fix login crash",
        "body": "Steps to reproduce",
        "labels": [{"name": "pa:fix"}],
        "html_url": "https://github.test/o/r/issues/12",
    },
    "repository": {"full_name": "o/r"},
    "sender": {"login": "owner"},
}


def test_github_issue_parse():
    event = GitHubWebhookAdapter().parse({**ISSUE_PAYLOAD, "_event": "issues"})
    assert event.source == "github" and event.type == "github.issue.opened"
    assert event.payload["repo"] == "o/r" and event.payload["labels"] == ["pa:fix"]
    assert event.threads == ["github:o/r:issues:12"]
    assert event.dedup_key == "github:o/r:issue:12:opened"


def test_github_comment_parse_on_pr():
    payload = {
        "action": "created",
        "issue": {"number": 7},
        "pull_request": {"number": 7},
        "comment": {"id": 99, "body": "再改一下 X", "html_url": "https://github.test/c/99"},
        "repository": {"full_name": "o/r"},
        "sender": {"login": "owner"},
    }
    event = GitHubWebhookAdapter().parse({**payload, "_event": "issue_comment"})
    assert event.type == "github.comment.created"
    assert event.payload["on_pr"] is True
    assert event.threads == ["github:o/r:pulls:7"]


def test_gitea_issue_parse():
    payload = {
        "action": "opened",
        "issue": {"number": 5, "title": "T", "body": "B", "html_url": "https://gitea.test/5"},
        "repository": {"full_name": "o/r"},
        "sender": {"login": "owner"},
    }
    event = GiteaWebhookAdapter().parse({**payload, "_event": "issues"})
    assert event.source == "gitea" and event.type == "gitea.issue.opened"


def test_unsupported_event_type_raises():
    try:
        GitHubWebhookAdapter().parse({"_event": "push"})
        raise AssertionError("must raise")
    except ValueError:
        pass


def _app(configured, secrets_map):
    app = create_app(
        configured, api_token="tok", webhook_secrets=secrets_map
    )
    return TestClient(app)


def test_webhook_endpoint_rejects_bad_signature(configured):
    client = _app(configured, {"github": "s3c"})
    body = json.dumps(ISSUE_PAYLOAD).encode()
    response = client.post(
        "/hooks/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=wrong"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "webhook_rejected"


def test_webhook_endpoint_accepts_valid_and_dedups(configured):
    client = _app(configured, {"github": "s3c"})
    body = json.dumps(ISSUE_PAYLOAD).encode()
    headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signed("s3c", body),
        "X-GitHub-Delivery": "d-1",
    }
    first = client.post("/hooks/github", content=body, headers=headers)
    assert first.status_code == 200, first.text
    result = first.json()
    # github sender is not in the owner allowlist yet: recorded as observed
    # (PRD §6.1). Delivery idempotency is what matters here.
    assert result["event"]["status"] in ("accepted", "observed")

    # Same delivery id: idempotent no-op.
    second = client.post("/hooks/github", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


def test_webhook_unconfigured_returns_404(configured):
    client = _app(configured, {})
    response = client.post(
        "/hooks/github", content=b"{}", headers={"X-Hub-Signature-256": "sha256=x"}
    )
    assert response.status_code == 404
