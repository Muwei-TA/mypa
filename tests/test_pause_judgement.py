import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from pa.engines.base import EngineResult
from pa.hub.dispatcher import recover_expired
from pa.ingress.manual import ManualAdapter
from pa.procs import checkpoints
from pa.procs.worker import Worker
from pa.skills.receipt import Receipt, Usage
from pa.store.db import transaction
from pa.store.facades import OwnerFacade


def emit(facade):
    return facade.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "文本"},
            }
        )
    )["tasks"][0]


def result(kind=None, *, needs_owner=False, turns=1):
    return EngineResult(
        engine_id="fake",
        turns=turns,
        error="simulated" if kind else None,
        error_kind=kind,
        usage=Usage(input_tokens=4, output_tokens=2),
        receipt=None
        if kind
        else Receipt(
            status="needs_human" if needs_owner else "succeeded",
            summary="已完成执行",
            code_changed=False,
            confidence=1,
            risk="low",
            evidence=[],
            usage=Usage(),
        ),
    )


class Engine:
    def __init__(self, facade, *, kind=None, pause=True, needs_owner=False):
        self.facade, self.kind, self.pause = facade, kind, pause
        self.needs_owner = needs_owner
        self.calls = 0

    async def health(self):
        return {"installed": True}

    async def run(self, request):
        self.calls += 1
        if self.pause:
            self.facade.pause()
        return result(self.kind, needs_owner=self.needs_owner)


def once(facade, config_dir, engine=None, **kwargs):
    return asyncio.run(
        Worker(facade, config_dir, engines={"fake": engine} if engine else {}, **kwargs).once()
    )


def count(facade, table):
    with facade.engine.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def expire_checkpoint(facade):
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with transaction(facade.engine) as c:
        c.execute(text("UPDATE worker_checkpoints SET lease_until=:at"), {"at": past})
        c.execute(text("UPDATE tasks SET lease_until=:at WHERE status='running'"), {"at": past})


def test_pause_defers_success_without_discarding_usage(configured, config_dir):
    task_id = emit(configured)
    engine = Engine(configured)
    outcome = once(configured, config_dir, engine)
    assert outcome == {"task_id": task_id, "status": "running", "deferred": True}
    task = configured.show("tasks", task_id)
    assert [entry["to_status"] for entry in task["timeline"]] == ["leased", "running"]
    assert task["lease_until"] is None and task["lease_engine_id"] is None
    assert count(configured, "decisions") == count(configured, "effects") == 0
    assert count(configured, "cost_ledger") == count(configured, "worker_checkpoints") == 1
    assert recover_expired(configured.engine) == []
    assert once(configured, config_dir, engine) is None and engine.calls == 1


def test_new_worker_judges_saved_receipt_without_engine(configured, config_dir):
    task_id = emit(configured)
    engine = Engine(configured)
    once(configured, config_dir, engine)
    configured.resume()
    restarted = OwnerFacade(config_dir)
    try:
        outcome = once(restarted, config_dir)
        assert outcome["task_id"] == task_id and outcome["status"] == "closed"
        assert engine.calls == 1 and count(restarted, "runs") == 1
        assert count(restarted, "worker_checkpoints") == 0
        assert once(restarted, config_dir) is None
    finally:
        restarted.close()


def test_pause_defers_failure_and_redispatch_until_resume(configured, config_dir):
    task_id = emit(configured)
    engine = Engine(configured, kind="fatal")
    assert once(configured, config_dir, engine)["deferred"]
    assert configured.show("tasks", task_id)["retry_count"] == 0
    configured.resume()
    assert once(configured, config_dir)["retry_count"] == 1
    assert engine.calls == 1 and count(configured, "worker_checkpoints") == 0


def test_repeated_pauses_do_not_reset_transient_retry_counter(configured, config_dir):
    task_id = emit(configured)
    engine = Engine(configured, kind="transient")

    async def no_sleep(_):
        pass

    for _ in range(4):
        outcome = once(configured, config_dir, engine, sleeper=no_sleep)
        assert outcome["deferred"]
        configured.resume()
    outcome = once(configured, config_dir, engine, sleeper=no_sleep)
    assert outcome["status"] == "queued" and outcome["retry_count"] == 1
    assert engine.calls == 4
    assert configured.show("tasks", task_id)["retry_count"] == 1


def test_pause_during_backoff_keeps_next_attempt_checkpoint(configured, config_dir):
    emit(configured)

    class FirstFailure(Engine):
        async def run(self, request):
            self.calls += 1
            return result("transient" if self.calls == 1 else None)

    async def pause_on_backoff(_):
        configured.pause()

    engine = FirstFailure(configured)
    assert once(configured, config_dir, engine, sleeper=pause_on_backoff)["deferred"]
    assert engine.calls == 1
    configured.resume()
    assert once(configured, config_dir, engine)["status"] == "closed"
    assert engine.calls == 2 and count(configured, "cost_ledger") == 2


def test_paused_human_decision_starts_deadline_only_after_resume(configured, config_dir):
    emit(configured)
    engine = Engine(configured, needs_owner=True)
    assert once(configured, config_dir, engine)["deferred"]
    assert count(configured, "decisions") == count(configured, "effects") == 0
    configured.resume()
    resumed = datetime.now(UTC)
    assert once(configured, config_dir)["status"] == "awaiting_decision"
    with configured.engine.connect() as c:
        expires = c.execute(text("SELECT expires_at FROM decisions")).scalar_one()
    assert datetime.fromisoformat(expires) >= resumed + timedelta(hours=24)
    assert count(configured, "effects") == 1


def test_cancelled_pending_result_is_not_judged(configured, config_dir):
    task_id = emit(configured)
    engine = Engine(configured)
    once(configured, config_dir, engine)
    configured.cancel(task_id)
    configured.resume()
    assert once(configured, config_dir) is None
    assert count(configured, "worker_checkpoints") == 0
    assert engine.calls == 1 and configured.show("tasks", task_id)["status"] == "cancelled"


def test_crash_after_record_replays_result_before_lease_recovery(configured, config_dir):
    task_id = emit(configured)
    engine = Engine(configured, pause=False)

    class Crash(Worker):
        async def _finalize(self, *args):
            raise RuntimeError("simulated crash before judgement")

    with pytest.raises(RuntimeError):
        asyncio.run(Crash(configured, config_dir, engines={"fake": engine}).once())
    assert count(configured, "worker_checkpoints") == 1
    expire_checkpoint(configured)
    assert once(configured, config_dir)["status"] == "closed"
    assert count(configured, "runs") == engine.calls == 1
    assert [row["to_status"] for row in configured.show("tasks", task_id)["timeline"]] == [
        "leased",
        "running",
        "succeeded",
        "judged",
        "closed",
    ]


@pytest.mark.parametrize("kind", [None, "fatal", "task_timeout"])
def test_pause_racing_with_judgement_is_checked_in_write_transaction(configured, config_dir, kind):
    emit(configured)
    engine = Engine(configured, kind=kind, pause=False)

    class RacingPause(Worker):
        async def _finalize(self, *args):
            configured.pause()
            return await super()._finalize(*args)

        def _requeue_or_escalate(self, *args):
            configured.pause()
            return super()._requeue_or_escalate(*args)

        def _escalate_limit(self, *args):
            configured.pause()
            return super()._escalate_limit(*args)

    outcome = asyncio.run(RacingPause(configured, config_dir, engines={"fake": engine}).once())
    assert outcome["deferred"] and count(configured, "decisions") == 0
    configured.resume()
    outcome = once(configured, config_dir)
    assert (
        outcome["status"]
        == ({None: "closed", "fatal": "queued", "task_timeout": "awaiting_decision"}[kind])
    )
    assert engine.calls == 1


def test_pending_checkpoint_is_claimed_by_only_one_worker(configured, config_dir):
    emit(configured)
    once(configured, config_dir, Engine(configured))
    configured.resume()
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: checkpoints.take(configured.engine), range(4)))
    assert sum(claim is not None for claim in claims) == 1


def test_judgement_rollback_preserves_checkpoint_and_retries_once(
    configured, config_dir, monkeypatch
):
    emit(configured)
    engine = Engine(configured, pause=False, needs_owner=True)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated notification enqueue failure")

    with monkeypatch.context() as patch:
        patch.setattr("pa.procs.worker.request_notification", fail)
        with pytest.raises(RuntimeError):
            once(configured, config_dir, engine)
    assert count(configured, "decisions") == count(configured, "effects") == 0
    assert count(configured, "worker_checkpoints") == 1
    expire_checkpoint(configured)
    assert once(configured, config_dir)["status"] == "awaiting_decision"
    assert count(configured, "decisions") == count(configured, "effects") == 1
    assert count(configured, "worker_checkpoints") == 0 and engine.calls == 1


def test_resumed_retry_rechecks_engine_capacity(configured, config_dir):
    emit(configured)

    class FirstFailure(Engine):
        async def run(self, request):
            self.calls += 1
            return result("transient" if self.calls == 1 else None)

    async def pause_on_backoff(_):
        configured.pause()

    engine = FirstFailure(configured)
    assert once(configured, config_dir, engine, sleeper=pause_on_backoff)["deferred"]
    with transaction(configured.engine) as c:
        c.execute(
            text("""
            INSERT INTO tasks(id,skill_id,status,priority,lease_owner,lease_engine_id)
            VALUES('busy','other-skill','running','P2','other-worker','fake')
        """)
        )
    configured.resume()
    assert once(configured, config_dir, engine)["deferred"] and engine.calls == 1
    configured.cancel("busy")
    assert once(configured, config_dir, engine)["status"] == "closed"
    assert engine.calls == count(configured, "runs") == 2


def test_checkpoint_payload_is_redacted(configured, config_dir):
    emit(configured)

    class SecretSummary(Engine):
        async def run(self, request):
            self.facade.pause()
            response = result(needs_owner=True)
            response.receipt.summary = "Summary sk-checkpoint-secret-example"
            return response

    assert once(configured, config_dir, SecretSummary(configured))["deferred"]
    with configured.engine.connect() as c:
        payload = c.execute(text("SELECT payload_json FROM worker_checkpoints")).scalar_one()
    assert "sk-checkpoint-secret-example" not in payload
