"""Workspace management (M2-04, PRD §9.1): mirror, worktree, write locks.

Layout: <data>/repos/<scm>/<owner>/<repo>.git (bare mirror) and
<data>/workspaces/<task_id>/ (git worktree on branch pa/<task_id>).
Repo write locks serialize code tasks per repository; readers run parallel.
"""

import asyncio
import os
import subprocess
from pathlib import Path

from pa.common import PAError


def _git(cwd: Path, *args: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )
    if result.returncode != 0:
        raise PAError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def mirror_path(data_dir: Path, scm: str, repo: str) -> Path:
    owner, name = repo.split("/", 1)
    return data_dir / "repos" / scm / owner / f"{name}.git"


def ensure_mirror(data_dir: Path, scm: str, repo: str, remote_url: str) -> Path:
    path = mirror_path(data_dir, scm, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _git(path.parent, "clone", "--mirror", remote_url, str(path))
    else:
        _git(path, "remote", "update", "--prune")
    return path


def add_worktree(data_dir: Path, mirror: Path, task_id: str, base_ref: str) -> Path:
    workspaces = data_dir / "workspaces"
    workspaces.mkdir(parents=True, exist_ok=True)
    target = workspaces / task_id
    if target.exists():
        raise PAError(f"workspace already exists: {target}", "conflict")
    branch = f"pa/{task_id}"
    _git(
        Path(mirror),
        "worktree",
        "add",
        "-b",
        branch,
        str(target),
        base_ref,
    )
    return target


def is_task_branch(branch: str, task_id: str) -> bool:
    """PRD §9.1: a PR whose branch tail equals pa/<task_id> belongs to the task."""
    parts = branch.split("/")
    return "/".join(parts[-2:]) == f"pa/{task_id}"


class RepoWriteLock:
    """In-process async lock per repository; cross-process safety comes from
    the database lease (dispatcher) — this guard prevents local races."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    def _get(self, repo: str) -> asyncio.Lock:
        if repo not in self._locks:
            self._locks[repo] = asyncio.Lock()
        return self._locks[repo]

    async def acquire(self, repo: str):
        return await self._get(repo).acquire()

    def release(self, repo: str):
        lock = self._locks.get(repo)
        if lock and lock.locked():
            lock.release()


def prepare_workspace(
    data_dir: Path,
    *,
    scm: str,
    repo: str,
    remote_url: str,
    task_id: str,
    base_ref: str = "main",
    prepare_command: list[str] | None = None,
    timeout_min: int = 10,
) -> dict:
    """Fetch mirror, create worktree, run the Skill prepare command (D13)."""
    mirror = ensure_mirror(data_dir, scm, repo, remote_url)
    worktree = add_worktree(data_dir, mirror, task_id, base_ref)
    output = ""
    if prepare_command:
        try:
            result = subprocess.run(
                prepare_command,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=timeout_min * 60,
                check=False,
            )
            output = (result.stdout + result.stderr)[-4000:]
            if result.returncode != 0:
                raise PAError(f"prepare failed ({result.returncode}): {output[:300]}")
        except subprocess.TimeoutExpired:
            raise PAError(f"prepare timed out after {timeout_min} minutes") from None
    return {"mirror": str(mirror), "worktree": str(worktree), "branch": f"pa/{task_id}"}


def find_existing_pr_branch(worktree: Path) -> str | None:
    """Recovery (D2): the task branch survives restarts in the worktree."""
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    return branch or None
