"""GitHub webhook adapter (M2-03): routes payloads into the hub pipeline."""

from pa.ingress.base import Event, IngressAdapter
from pa.ingress.webhook_common import (
    parse_gitea_comment,
    parse_gitea_issue,
    parse_gitea_pr,
    parse_github_comment,
    parse_github_issue,
    parse_github_pr,
)


class GitHubWebhookAdapter(IngressAdapter):
    name = "github"

    def parse(self, request: dict) -> Event:
        event_type = request.get("_event", "")
        if event_type == "issues":
            return parse_github_issue(request)
        if event_type == "pull_request":
            return parse_github_pr(request)
        if event_type in ("issue_comment", "pull_request_review_comment"):
            return parse_github_comment(request)
        raise ValueError(f"unsupported github webhook: {event_type}")


class GiteaWebhookAdapter(IngressAdapter):
    name = "gitea"

    def parse(self, request: dict) -> Event:
        event_type = request.get("_event", "")
        if event_type == "issues":
            return parse_gitea_issue(request)
        if event_type == "pull_request":
            return parse_gitea_pr(request)
        if event_type in ("issue_comment", "pull_request_review_comment"):
            return parse_gitea_comment(request)
        raise ValueError(f"unsupported gitea webhook: {event_type}")
