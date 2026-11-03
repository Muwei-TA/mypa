"""A task's execution allowance survives repairs, failover and redispatch."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from pa.config.loader import load
from pa.config.setup import save_files
from pa.engines.base import EngineResult
from pa.engines.process import run_process
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.loader import load_skill
from pa.skills.receipt import Receipt, Usage
from pa.store.db import transaction


@pytest.fixture
def allowance(monkeypatch):
    def set_limits(*, tokens=100, seconds=60):
        skill = load_skill().model_copy(deep=True)
        skill.budget.per_task_tokens = tokens
        skill.timeout_min = seconds / 60
        monkeypatch.setattr("pa.procs.worker.load_skill", lambda _: skill, raising=False)

    set_limits()
    return set_limits


def emit(facade):
    return facade.emit(
        ManualAdapter().parse(
            {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "概括文本"}}
        )
    )["tasks"][0]


def result(*, kind=None, tokens=0, seconds=0):
    usage = Usage(input_tokens=tokens, duration_s=seconds)
    return EngineResult(
        engine_id="fake",
        usage=usage,
        error="simulated" if kind else None,
        error_kind=kind,
        receipt=None
        if kind
        else Receipt(
            status="succeeded",
            summary="完成",
            code_changed=False,
            confidence=1,
            risk="low",
            evidence=[],
            usage=Usage(),
        ),
    )


class Engine:
    def __init__(self, *results):
        self.results = results
        self.calls = 0

    async def health(self):
        return {"installed": True}

    async def run(self, request):
        value = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return value.model_copy(deep=True)


async def no_sleep(_):
    pass


def run(facade, config_dir, engine):
    return asyncio.run(
        Worker(facade, config_dir, engines={"fake": engine}, sleeper=no_sleep).once()
    )


def assert_escalated(facade, task_id, outcome, reason):
    assert outcome["status"] == "awaiting_decision"
    assert outcome["reason"] == reason
    task = facade.show("tasks", task_id)
    assert task["lease_owner"] is None and task["lease_until"] is None
    with facade.engine.connect() as c:
        decisions = (
            c.execute(text("SELECT reason_json FROM decisions WHERE task_id=:id"), {"id": task_id})
            .scalars()
            .all()
        )
    assert len(decisions) == 1 and reason in decisions[0]
    effects = facade.list("effects")["items"]
    assert len(effects) == 1  # P1 must not wait in the P2 merge buffer.
    assert reason in json.dumps(task["timeline"])


def test_transient_calls_share_token_allowance(configured, config_dir, allowance):
    task_id = emit(configured)
    engine = Engine(result(kind="transient", tokens=60))
    outcome = run(configured, config_dir, engine)
    assert_escalated(configured, task_id, outcome, "task_token_limit")
    assert engine.calls == 2


def test_exhausted_call_does_not_attempt_receipt_repair(configured, config_dir, allowance):
    task_id = emit(configured)
    engine = Engine(result(kind="invalid_output", tokens=100))
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_token_limit")
    assert engine.calls == 1


def test_repair_usage_can_exhaust_allowance(configured, config_dir, allowance):
    task_id = emit(configured)
    engine = Engine(result(kind="invalid_output", tokens=60), result(tokens=50))
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_token_limit")
    with configured.engine.connect() as c:
        assert c.execute(text("SELECT sum(input_tokens) FROM cost_ledger")).scalar_one() == 110
    assert engine.calls == 2


def test_failover_shares_allowance(configured, config_dir, allowance):
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {
                    name: {"adapter": "codex_cli", "command": [sys.executable]}
                    for name in ("fake", "other")
                },
                "roles": {"dev": ["fake", "other"]},
            }
        },
    )
    configured.config = load(config_dir)
    task_id = emit(configured)
    first = Engine(result(kind="quota", tokens=60))
    second = Engine(result(tokens=50))
    outcome = asyncio.run(
        Worker(configured, config_dir, engines={"fake": first, "other": second}).once()
    )
    assert_escalated(configured, task_id, outcome, "task_token_limit")
    assert first.calls == second.calls == 1


def test_redispatch_reloads_persisted_tokens(configured, config_dir, allowance):
    task_id = emit(configured)
    engine = Engine(result(kind="fatal", tokens=60))
    assert run(configured, config_dir, engine)["status"] == "queued"
    # A fresh Worker, as after a runtime restart, must include the previous run.
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_token_limit")
    assert engine.calls == 2


@pytest.mark.parametrize(
    "usage,reason",
    [
        ({"tokens": 100}, "task_token_limit"),
        ({"seconds": 60}, "task_timeout"),
    ],
)
def test_already_exhausted_task_never_calls_engine(
    configured, config_dir, allowance, usage, reason
):
    task_id = emit(configured)
    engine = Engine(result(kind="fatal", **usage))
    # Initially use a larger allowance, then tighten it before redispatch.
    allowance(tokens=200, seconds=120)
    assert run(configured, config_dir, engine)["status"] == "queued"
    allowance()
    assert_escalated(configured, task_id, run(configured, config_dir, engine), reason)
    assert engine.calls == 1


def test_timeout_cancels_running_engine_and_notifies(configured, config_dir, allowance):
    allowance(seconds=0.02)
    task_id = emit(configured)

    class Blocking(Engine):
        cancelled = False

        async def run(self, request):
            self.calls += 1
            try:
                await asyncio.sleep(0.2)
                return result()
            finally:
                self.cancelled = True

    engine = Blocking()
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_timeout")
    assert engine.calls == 1 and engine.cancelled
    with configured.engine.connect() as c:
        assert c.execute(text("SELECT sum(duration_s) FROM cost_ledger")).scalar_one() >= 0.02


def test_redispatch_reloads_persisted_execution_duration(configured, config_dir, allowance):
    task_id = emit(configured)
    engine = Engine(result(kind="fatal", seconds=40))
    assert run(configured, config_dir, engine)["status"] == "queued"
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_timeout")
    assert engine.calls == 2


def test_small_success_still_closes(configured, config_dir, allowance):
    emit(configured)
    engine = Engine(result(tokens=40))
    assert run(configured, config_dir, engine)["status"] == "closed"


def test_input_and_output_tokens_both_count_at_boundary(configured, config_dir, allowance):
    task_id = emit(configured)
    response = result(tokens=40)
    response.usage.output_tokens = 60
    engine = Engine(response)
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_token_limit")
    assert engine.calls == 1


def test_shutdown_during_repair_preserves_completed_usage(configured, config_dir, allowance):
    task_id = emit(configured)

    async def scenario():
        repairing = asyncio.Event()

        class Interrupted(Engine):
            async def run(self, request):
                self.calls += 1
                if self.calls == 1:
                    return result(kind="invalid_output", tokens=60)
                repairing.set()
                await asyncio.Future()

        pending = asyncio.create_task(
            Worker(configured, config_dir, engines={"fake": Interrupted()}).once()
        )
        await repairing.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
    with transaction(configured.engine) as c:
        assert c.execute(text("SELECT sum(input_tokens) FROM cost_ledger")).scalar_one() == 60
        c.execute(
            text("UPDATE tasks SET lease_until=:at WHERE id=:id"),
            {
                "id": task_id,
                "at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
    engine = Engine(result(tokens=50))
    assert_escalated(configured, task_id, run(configured, config_dir, engine), "task_token_limit")
    assert engine.calls == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process cleanup")
def test_task_timeout_reaps_real_engine_process(configured, config_dir, allowance, tmp_path):
    allowance(seconds=0.2)
    task_id = emit(configured)
    pid_file = tmp_path / "pid"

    class SubprocessEngine(Engine):
        async def run(self, request):
            await run_process(
                [
                    sys.executable,
                    "-c",
                    "import os,sys,time;from pathlib import Path;"
                    "Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)",
                    str(pid_file),
                ],
                cwd=tmp_path,
                timeout_s=30,
            )
            return result()

    assert_escalated(
        configured, task_id, run(configured, config_dir, SubprocessEngine()), "task_timeout"
    )
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
