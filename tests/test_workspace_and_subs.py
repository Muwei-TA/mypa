"""Workspace management (M2-04) and subscriptions (M2-07) tests."""

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from pa.hub.subscriptions import is_bot, prune_expired, route, subscribe
from pa.store.db import transaction
from pa.workspace.mirror import (
    add_worktree,
    ensure_mirror,
    is_task_branch,
    mirror_path,
    prepare_workspace,
)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=path, capture_output=True, check=True)

    git("init", "-b", "main")
    (path / "hello.txt").write_text("hello")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    return path


def test_ensure_mirror_and_worktree(tmp_path):
    source = _init_repo(tmp_path / "src")
    data = tmp_path / "data"
    mirror = ensure_mirror(data, "github", "o/r", str(source))
    assert mirror == mirror_path(data, "github", "o/r")
    assert (mirror / "HEAD").exists()

    # Re-run: refresh, not re-clone.
    again = ensure_mirror(data, "github", "o/r", str(source))
    assert again == mirror

    worktree = add_worktree(data, mirror, "task-1", "main")
    assert (worktree / "hello.txt").exists()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch == "pa/task-1"


def test_is_task_branch():
    assert is_task_branch("pa/task-1", "task-1")
    assert is_task_branch("feature/pa/task-2", "task-2")
    assert not is_task_branch("pa/other", "task-1")
    assert not is_task_branch("main", "task-1")


def test_prepare_workspace_runs_command(tmp_path):
    source = _init_repo(tmp_path / "src")
    data = tmp_path / "data"
    out = prepare_workspace(
        data,
        scm="github",
        repo="o/r",
        remote_url=str(source),
        task_id="task-3",
        base_ref="main",
        prepare_command=["python3", "-c", "print('prepared')"],
        timeout_min=1,
    )
    assert Path(out["worktree"]).exists()
    assert out["branch"] == "pa/task-3"


def test_prepare_failure_recorded(tmp_path):
    source = _init_repo(tmp_path / "src")
    data = tmp_path / "data"
    try:
        prepare_workspace(
            data,
            scm="github",
            repo="o/r",
            remote_url=str(source),
            task_id="task-4",
            prepare_command=["false"],
            timeout_min=1,
        )
        raise AssertionError("must fail")
    except Exception as exc:
        assert "prepare failed" in str(exc)


def _emit_task(facade, dedup):
    from pa.ingress.manual import ManualAdapter

    return facade.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "hi"},
                "dedup_key": dedup,
            }
        )
    )["tasks"][0]


def test_subscribe_and_route_running_task(configured):
    task = _emit_task(configured, "sub-1")
    with transaction(configured.engine) as c:
        subscribe(c, task_id=task, thread_ref="github:o/r:issues:12")
        out = route(
            c,
            configured.config,
            thread_ref="github:o/r:issues:12",
            actor="github:owner",
            body="再改一下 X",
        )
    assert out["action"] == "deliver" and out["task_id"] == task


def test_context_only_subscription_has_no_authority(configured):
    task = _emit_task(configured, "sub-2")
    with transaction(configured.engine) as c:
        subscribe(c, task_id=task, thread_ref="github:o/r:issues:13", context_only=True)
        out = route(
            c,
            configured.config,
            thread_ref="github:o/r:issues:13",
            actor="github:owner",
            body="context",
        )
    assert out["action"] == "context_only"


def test_bot_cannot_wake_ended_task(configured):
    task = _emit_task(configured, "sub-3")
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE tasks SET status='closed' WHERE id=:t"), {"t": task})
    with transaction(configured.engine) as c:
        subscribe(c, task_id=task, thread_ref="github:o/r:issues:14")
        bot = route(
            c,
            configured.config,
            thread_ref="github:o/r:issues:14",
            actor="github:dependabot[bot]",
            body="bump",
        )
        human = route(
            c,
            configured.config,
            thread_ref="github:o/r:issues:14",
            actor="github:owner",
            body="再改一下",
        )
    assert bot["action"] == "none" and "bot" in bot["reason"]
    # closed with an inferred mandate in observe mode: mandate invalid -> reenter.
    assert human["action"] in ("resume", "reenter")


def test_subscription_expiry(configured):
    task = _emit_task(configured, "sub-4")
    with transaction(configured.engine) as c:
        subscribe(c, task_id=task, thread_ref="github:o/r:issues:15")
        # Force an already-expired subscription.
        past = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        c.execute(
            text("UPDATE subscriptions SET expires_at=:e WHERE thread_ref=:th"),
            {"e": past, "th": "github:o/r:issues:15"},
        )
    with transaction(configured.engine) as c:
        out = route(
            c,
            configured.config,
            thread_ref="github:o/r:issues:15",
            actor="github:owner",
            body="hi",
        )
    assert out["action"] == "none" and out["reason"] == "subscription expired"


def test_prune_expired_only_ended_tasks(configured):
    task = _emit_task(configured, "sub-5")
    with transaction(configured.engine) as c:
        subscribe(c, task_id=task, thread_ref="github:o/r:issues:16")
        past = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        c.execute(
            text("UPDATE subscriptions SET expires_at=:e WHERE thread_ref=:th"),
            {"e": past, "th": "github:o/r:issues:16"},
        )
        # Task still queued: not pruned.
        first = prune_expired(c)
    assert first == 0
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE tasks SET status='closed' WHERE id=:t"), {"t": task})
        second = prune_expired(c)
    assert second == 1


def test_is_bot_variants():
    assert is_bot("github:dependabot[bot]")
    assert is_bot("github:actions-bot")
    assert is_bot("github:github-actions")
    assert not is_bot("github:owner")
