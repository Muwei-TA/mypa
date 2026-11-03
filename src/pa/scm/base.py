"""SCM adapter contract (PRD §10.1): every platform write goes through here."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class Issue:
    number: int
    title: str
    body: str
    author: str
    labels: list[str] = field(default_factory=list)
    url: str | None = None


@dataclass
class PR:
    number: int
    head: str
    base: str
    title: str
    body: str
    state: str  # open | closed | merged
    sha: str | None = None
    url: str | None = None


@dataclass
class Diff:
    files: list[dict[str, Any]]  # [{path, added, removed, change_type, patch}]
    additions: int
    deletions: int

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass
class CheckSummary:
    state: str  # success | failure | pending | unknown
    contexts: list[dict[str, Any]] = field(default_factory=list)
    required: list[str] = field(default_factory=list)

    @property
    def all_required_passed(self) -> bool:
        latest = {c["context"]: c["state"] for c in self.contexts}
        return self.state == "success" and all(latest.get(c) == "success" for c in self.required)


@dataclass
class MergeResult:
    merged: bool
    sha: str | None
    method: str


@dataclass
class RunRef:
    id: str
    url: str | None = None


@runtime_checkable
class ScmAdapter(Protocol):
    async def get_issue(self, repo: str, number: int) -> Issue: ...

    async def open_pr(
        self, repo: str, head: str, base: str, title: str, body: str, labels: list[str]
    ) -> PR: ...

    async def comment(self, repo: str, number: int, body: str) -> None: ...

    async def get_diff(self, repo: str, pr: PR) -> Diff: ...

    async def get_checks(self, repo: str, pr: PR) -> CheckSummary: ...

    async def merge(
        self, repo: str, pr: PR, method: Literal["squash", "merge", "rebase"]
    ) -> MergeResult: ...

    async def dispatch_workflow(
        self, repo: str, workflow: str, ref: str, inputs: dict[str, str]
    ) -> RunRef: ...

    async def set_status(
        self,
        repo: str,
        sha: str,
        context: str,
        state: str,
        description: str = "",
        url: str | None = None,
    ) -> None: ...

    async def find_pr_by_branch(self, repo: str, branch: str) -> PR | None: ...

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...
