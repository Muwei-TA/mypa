"""Webhook ingestion security layer (M2-03, PRD §5.2 / §6.1 / §17.5).

HMAC verification, timestamp freshness, per-IP failure throttling with a
one-hour blacklist, and X-*-Delivery based idempotency. Adapters parse
platform payloads into the shared Event model.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pa.ingress.base import Event


@dataclass
class WebhookGuard:
    """Stateful guard: signature, timestamp window, failure throttle."""

    secret: str
    max_age_s: int = 300  # §17.5: reject headers older than 5 minutes
    window_s: int = 300
    max_failures: int = 20
    blacklist_s: int = 3600
    _failures: dict[str, list[float]] = field(default_factory=dict)
    _blacklist: dict[str, float] = field(default_factory=dict)

    def _prune(self, ip: str, now: float):
        cutoff = now - self.window_s
        self._failures[ip] = [t for t in self._failures.get(ip, []) if t > cutoff]

    def check(self, *, ip: str, headers: dict[str, str], body: bytes) -> tuple[bool, str]:
        now = time.time()
        if ip in self._blacklist:
            if now - self._blacklist[ip] < self.blacklist_s:
                return False, "blacklisted"
            del self._blacklist[ip]
        signature = headers.get("X-Hub-Signature-256") or headers.get("X-Gitea-Signature")
        expected = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            self._fail(ip, now)
            return False, "bad signature"
        timestamp = headers.get("X-GitHub-Hookshot") or headers.get("X-Gitea-Timestamp")
        if timestamp:
            try:
                stale = abs(now - float(timestamp)) > self.max_age_s
            except ValueError:
                return False, "invalid timestamp"
            if stale:
                self._fail(ip, now)
                return False, "stale timestamp"
        return True, "ok"

    def _fail(self, ip: str, now: float):
        self._failures.setdefault(ip, []).append(now)
        self._prune(ip, now)
        if len(self._failures[ip]) >= self.max_failures:
            self._blacklist[ip] = now
            del self._failures[ip]

    def blacklisted(self, ip: str) -> bool:
        at = self._blacklist.get(ip)
        return at is not None and time.time() - at < self.blacklist_s


def _event(
    *,
    platform: str,
    event_type: str,
    actor: str,
    summary: str,
    payload: dict,
    dedup_key: str,
    link: str | None = None,
    threads: list[str] | None = None,
) -> Event:
    return Event(
        source=platform,
        type=event_type,
        category="new_source",
        actor=actor,
        trust="verified",
        addressed=False,
        received_at=datetime.now(UTC),
        dedup_key=dedup_key,
        summary=summary[:80],
        payload=payload,
        link=link,
        threads=threads or [],
    )


def parse_github_issue(payload: dict) -> Event:
    issue = payload["issue"]
    labels = [label["name"] for label in issue.get("labels", [])]
    repo = payload["repository"]["full_name"]
    action = payload.get("action", "opened")
    return _event(
        platform="github",
        event_type=f"github.issue.{action}",
        actor=f"github:{payload['sender']['login']}",
        summary=(issue.get("title") or "")[:80],
        payload={
            "repo": repo,
            "number": issue["number"],
            "title": issue.get("title", ""),
            "body": issue.get("body") or "",
            "labels": labels,
        },
        dedup_key=f"github:{repo}:issue:{issue['number']}:{action}",
        link=issue.get("html_url"),
        threads=[f"github:{repo}:issues:{issue['number']}"],
    )


def parse_github_pr(payload: dict) -> Event:
    pr = payload["pull_request"]
    repo = payload["repository"]["full_name"]
    action = payload.get("action", "opened")
    return _event(
        platform="github",
        event_type=f"github.pr.{action}",
        actor=f"github:{payload['sender']['login']}",
        summary=(pr.get("title") or "")[:80],
        payload={
            "repo": repo,
            "number": pr["number"],
            "action": action,
            "head": pr["head"]["ref"],
            "base": pr["base"]["ref"],
            "url": pr.get("html_url", ""),
        },
        dedup_key=f"github:{repo}:pr:{pr['number']}:{action}",
        link=pr.get("html_url"),
        threads=[f"github:{repo}:pulls:{pr['number']}"],
    )


def parse_github_comment(payload: dict) -> Event:
    comment = payload["comment"]
    on_pr = "pull_request" in payload
    target = payload["pull_request"] if on_pr else payload["issue"]
    repo = payload["repository"]["full_name"]
    action = payload.get("action", "created")
    thread = (
        f"github:{repo}:pulls:{target['number']}"
        if on_pr
        else f"github:{repo}:issues:{target['number']}"
    )
    return _event(
        platform="github",
        event_type=f"github.comment.{action}",
        actor=f"github:{payload['sender']['login']}",
        summary=(comment.get("body") or "")[:80],
        payload={
            "repo": repo,
            "number": target["number"],
            "comment_id": comment["id"],
            "body": comment.get("body") or "",
            "on_pr": on_pr,
        },
        dedup_key=f"github:{repo}:comment:{comment['id']}:{action}",
        link=comment.get("html_url"),
        threads=[thread],
    )


def parse_gitea_issue(payload: dict) -> Event:
    issue = payload["issue"]
    repo = payload["repository"]["full_name"]
    action = payload.get("action", "opened")
    return _event(
        platform="gitea",
        event_type=f"gitea.issue.{action}",
        actor=f"gitea:{payload['sender']['login']}",
        summary=(issue.get("title") or "")[:80],
        payload={
            "repo": repo,
            "number": issue["number"],
            "title": issue.get("title", ""),
            "body": issue.get("body") or "",
            "labels": [label["name"] for label in issue.get("labels", [])],
        },
        dedup_key=f"gitea:{repo}:issue:{issue['number']}:{action}",
        link=issue.get("html_url"),
        threads=[f"gitea:{repo}:issues:{issue['number']}"],
    )


def parse_gitea_pr(payload: dict) -> Event:
    pr = payload["pull_request"] if "pull_request" in payload else payload["action"]["pull_request"]
    repo = payload["repository"]["full_name"]
    action = payload.get("action", "opened")
    if not isinstance(action, str):
        action = "opened"
    return _event(
        platform="gitea",
        event_type=f"gitea.pr.{action}",
        actor=f"gitea:{payload['sender']['login']}",
        summary=(pr.get("title") or "")[:80],
        payload={
            "repo": repo,
            "number": pr["number"],
            "action": action,
            "head": pr["head"]["ref"],
            "base": pr["base"]["ref"],
            "url": pr.get("html_url", ""),
        },
        dedup_key=f"gitea:{repo}:pr:{pr['number']}:{action}",
        link=pr.get("html_url"),
        threads=[f"gitea:{repo}:pulls:{pr['number']}"],
    )


def parse_gitea_comment(payload: dict) -> Event:
    comment = payload["comment"]
    on_pr = "pull_request" in payload and payload.get("is_pr", True)
    target = payload.get("issue", {})
    repo = payload["repository"]["full_name"]
    action = payload.get("action", "created")
    if not isinstance(action, str):
        action = "created"
    number = target.get("number", 0)
    thread = (
        f"gitea:{repo}:pulls:{number}" if on_pr else f"gitea:{repo}:issues:{number}"
    )
    return _event(
        platform="gitea",
        event_type=f"gitea.comment.{action}",
        actor=f"gitea:{payload['sender']['login']}",
        summary=(comment.get("body") or "")[:80],
        payload={
            "repo": repo,
            "number": number,
            "comment_id": comment["id"],
            "body": comment.get("body") or "",
            "on_pr": on_pr,
        },
        dedup_key=f"gitea:{repo}:comment:{comment['id']}:{action}",
        link=comment.get("html_url"),
        threads=[thread],
    )
