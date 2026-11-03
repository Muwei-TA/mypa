"""M1 end-to-end acceptance in an isolated environment (E2E-01/02/03/04).

Runs the real `pa` CLI and runtime against a throwaway config dir, SQLite
database and the scripted fake engine in `tests/e2e/fake_engine.py`. Feishu is
not configured here, so notification assertions cover outbox/buffer state,
not real message delivery. E2E-02 substitutes launchd with a supervisor
restart of the killed child process; real launchd behaviour stays a Mac mini
acceptance item. E2E-04 uses a cron-sourced inferred event because manual
events are explicit requests and bypass accept_mode by design (I-HUB-09).

Run: uv run --frozen pytest tests/e2e -v
"""

import json
import os
import select
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

E2E_DIR = Path(__file__).parent
PA = [sys.executable, "-m", "pa"]


def _run(args, config_dir, **kwargs):
    return subprocess.run(
        PA + ["--config-dir", str(config_dir)] + args,
        capture_output=True,
        text=True,
        timeout=120,
        **kwargs,
    )


def _wait_for(predicate, timeout=20, interval=0.3, message="condition not met"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(message)


def _db(config_dir, query, params=None):
    from pa.config.loader import load
    from pa.store.db import make_engine

    config = load(config_dir)
    engine = make_engine(config.main.data_path(config_dir) / "pa.sqlite3")
    try:
        with engine.connect() as c:
            return [dict(r) for r in c.execute(text(query), params or {}).mappings()]
    finally:
        engine.dispose()


@pytest.fixture
def e2e_env(tmp_path):
    config_dir = tmp_path / "config"
    result = _run(["init"], config_dir)
    assert result.returncode == 0, result.stderr
    rules = {
        "rules": [
            {
                "id": "r-e2e-direct",
                "rank": 1000,
                "match": {"source": "manual", "skill": "sample-direct"},
                "skill": "sample-direct",
                "accept_mode": "auto",
            },
            {
                "id": "r-e2e-observe",
                "rank": 2000,
                "match": {"source": "cron", "type": "cron.alert"},
                "skill": "sample-direct",
                "accept_mode": "observe",
            },
        ]
    }
    engines = {
        "engines": {
            "fake": {
                "adapter": "codex_cli",
                "command": [sys.executable, str(E2E_DIR / "fake_engine.py")],
                "timeout_s": 10,
            }
        },
        "roles": {"dev": ["fake"]},
    }
    config_dir.joinpath("rules.yaml").write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False)
    )
    config_dir.joinpath("engines.yaml").write_text(
        yaml.safe_dump(engines, allow_unicode=True, sort_keys=False)
    )
    result = _run(["doctor"], config_dir)
    assert result.returncode == 0, result.stderr
    return config_dir


@pytest.fixture
def e2e_env_notify(tmp_path):
    """Like e2e_env plus a fake Feishu source so notify paths produce work.

    Only used with `run --once`: the once-mode runtime never starts the Feishu
    long-connection subprocess, so no real connection is attempted.
    """
    config_dir = tmp_path / "config-notify"
    result = _run(["init"], config_dir)
    assert result.returncode == 0, result.stderr
    rules = {
        "rules": [
            {
                "id": "r-e2e-direct",
                "rank": 1000,
                "match": {"source": "manual", "skill": "sample-direct"},
                "skill": "sample-direct",
                "accept_mode": "auto",
                "notify": "result",
            }
        ]
    }
    engines = {
        "engines": {
            "fake": {
                "adapter": "codex_cli",
                "command": [sys.executable, str(E2E_DIR / "fake_engine.py")],
                "timeout_s": 10,
            }
        },
        "roles": {"dev": ["fake"]},
    }
    config_dir.joinpath("rules.yaml").write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False)
    )
    config_dir.joinpath("engines.yaml").write_text(
        yaml.safe_dump(engines, allow_unicode=True, sort_keys=False)
    )
    config_dir.joinpath("sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "feishu": {
                        "enabled": True,
                        "app_id": "cli_e2e_fake",
                        "app_secret": "secret://env/PA_TEST_FEISHU",
                    }
                }
            },
            sort_keys=False,
        )
    )
    config_dir.joinpath("notify.yaml").write_text(
        yaml.safe_dump({"target": {"type": "user", "id": "ou_e2e"}}, sort_keys=False)
    )
    result = _run(["doctor"], config_dir, env={**os.environ, "PA_TEST_FEISHU": "e2e-secret"})
    assert result.returncode == 0, result.stderr
    return config_dir


def _emit(config_dir, *, event_type="manual.test", payload, dedup_key):
    result = _run(
        [
            "event",
            "emit",
            event_type,
            "--payload",
            json.dumps(payload, ensure_ascii=False),
            "--dedup-key",
            dedup_key,
            "--json",
        ],
        config_dir,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_e2e_01_manual_emit_to_closed(e2e_env_notify):
    config_dir = e2e_env_notify
    created = _emit(
        config_dir,
        payload={"skill": "sample-direct", "text": "hello"},
        dedup_key="e2e-01",
    )
    assert created["event"]["status"] == "accepted"
    task_id = created["tasks"][0]

    run_once = _run(["run", "--once"], config_dir)
    assert run_once.returncode == 0, run_once.stderr

    rows = _db(config_dir, "SELECT status FROM tasks WHERE id=:id", {"id": task_id})
    assert rows[0]["status"] == "closed", rows
    # notify=result produced a P2 card: buffered before the merge window, or
    # already flushed into the outbox. Either artifact proves the chain ran.
    buffer_rows = _db(config_dir, "SELECT value FROM system_state WHERE key='notify_p2_buffer'")
    outbox = _db(config_dir, "SELECT status FROM effects")
    assert buffer_rows or outbox, "no notification artifact was produced"


def test_e2e_02_kill_worker_no_duplicate_execution(e2e_env):
    # "slow" makes the fake engine sleep so the kill lands mid-execution.
    created = _emit(
        e2e_env,
        payload={"skill": "sample-direct", "text": "slow hello"},
        dedup_key="e2e-02",
    )
    task_id = created["tasks"][0]

    process = subprocess.Popen(
        PA + ["--config-dir", str(e2e_env), "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "runtime did not start"
        assert json.loads(process.stdout.readline())["event"] == "runtime_started"
        _wait_for(
            lambda: _db(
                e2e_env,
                "SELECT 1 FROM runs WHERE task_id=:id AND started_at IS NOT NULL",
                {"id": task_id},
            ),
            message="run did not start",
        )
        os.kill(process.pid, 9)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    # Supervisor restart (launchd KeepAlive equivalent; boundary documented).
    # kill -9 leaves the 40s core_lock row behind: wait for its until timestamp
    # to lapse so the restarted runtime can take the lock, then wait out the
    # lease (timeout_s + 60s) for recovery to requeue and re-execute the task.

    def _lock_lapsed():
        rows = _db(e2e_env, "SELECT value FROM system_state WHERE key='core_lock'")
        if not rows:
            return True
        return datetime.fromisoformat(json.loads(rows[0]["value"])["until"]) < datetime.now(UTC)

    _wait_for(
        _lock_lapsed,
        timeout=60,
        interval=1,
        message="stale core_lock did not expire",
    )
    restarted = subprocess.Popen(
        PA + ["--config-dir", str(e2e_env), "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(
            lambda: _db(
                e2e_env, "SELECT 1 FROM tasks WHERE id=:id AND status='closed'", {"id": task_id}
            ),
            timeout=120,
            interval=2,
            message="task did not close after restart",
        )
    finally:
        restarted.terminate()
        try:
            restarted.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            restarted.kill()
            restarted.communicate(timeout=5)

    # Recovery must not re-execute the already-completed attempt: the killed
    # attempt either finished (closed directly) or the lease expired and the
    # checkpoint/recovery path continues without duplicating engine work.
    runs = _db(
        e2e_env,
        "SELECT attempt FROM runs WHERE task_id=:id ORDER BY attempt",
        {"id": task_id},
    )
    assert runs, "no run recorded"
    attempts = [r["attempt"] for r in runs]
    assert len(attempts) == len(set(attempts)), f"duplicate attempt rows: {attempts}"
    # Outbox actions must be at-most-once: the notification effect for the
    # closed task, if any, is either pending or done, never duplicated.
    effects = _db(
        e2e_env,
        "SELECT idem_key, count(*) AS n FROM effects GROUP BY idem_key HAVING n>1",
    )
    assert effects == [], f"duplicate effects: {effects}"


def test_e2e_03_pause_resume(e2e_env):
    paused = _run(["pause"], e2e_env)
    assert paused.returncode == 0, paused.stderr
    assert json.loads(paused.stdout)["paused"] is True

    created = _emit(
        e2e_env,
        payload={"skill": "sample-direct", "text": "hello"},
        dedup_key="e2e-03",
    )
    assert created["event"]["status"] == "accepted"
    task_id = created["tasks"][0]

    _run(["run", "--once"], e2e_env)
    rows = _db(e2e_env, "SELECT status FROM tasks WHERE id=:id", {"id": task_id})
    assert rows[0]["status"] == "queued", "paused runtime dispatched a task"

    resumed = _run(["resume"], e2e_env)
    assert json.loads(resumed.stdout)["paused"] is False

    run_once = _run(["run", "--once"], e2e_env)
    assert run_once.returncode == 0, run_once.stderr
    rows = _db(e2e_env, "SELECT status FROM tasks WHERE id=:id", {"id": task_id})
    assert rows[0]["status"] == "closed", rows


def _emit_cron(config_dir, *, event_type="cron.alert", payload, dedup_key):
    """Infer an event like the scheduler would (not an explicit request)."""
    script = (
        "import json\n"
        "from pa.ingress.cron import CronAdapter\n"
        "from pa.store.facades import OwnerFacade\n"
        "from pathlib import Path\n"
        "facade = OwnerFacade(Path(r'" + str(config_dir) + "'))\n"
        "event = CronAdapter().parse({\n"
        "    'id': '" + dedup_key.split(":")[0] + "',\n"
        "    'scheduled_at': '2026-11-02T00:00:00+00:00',\n"
        "    'event_type': '" + event_type + "',\n"
        "    'payload': json.loads(r'''" + json.dumps(payload, ensure_ascii=False) + "'''),\n"
        "})\n"
        "print(json.dumps(facade.emit(event)))\n"
        "facade.close()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_e2e_04_observe_rule_records_only(e2e_env):
    # Inferred cron event: observe mode must record, not create a task.
    created = _emit_cron(
        e2e_env,
        payload={"skill": "sample-direct", "text": "磁盘告警"},
        dedup_key="sched-x:2026-11-02T00:00:00+00:00",
    )
    assert created["event"]["status"] == "accepted"
    assert created["tasks"] == []

    observed = _run(["observe", "list", "--json"], e2e_env)
    assert observed.returncode == 0, observed.stderr
    items = json.loads(observed.stdout)["items"]
    assert any(i["rule_id"] == "r-e2e-observe" and i["would_accept"] == 1 for i in items), items

    _run(["run", "--once"], e2e_env)
    tasks = _db(e2e_env, "SELECT id FROM tasks")
    assert tasks == [], "observe rule created a task"
