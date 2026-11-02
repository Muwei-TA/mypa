import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from pa.config.loader import load
from pa.config.schema import EngineConfig
from pa.config.setup import save_files
from pa.engines.base import EngineRequest, EngineResult
from pa.engines.local import CodexCLI, HermesCLI, WorkBuddyCLI
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.loader import load_skill
from pa.skills.receipt import Receipt, Usage
from pa.store.db import make_engine, migrate, transaction


@pytest.fixture
def allowance(monkeypatch):
    def configure(turns):
        spec = load_skill().model_copy(deep=True)
        spec.budget.per_task_turns = turns
        monkeypatch.setattr("pa.procs.worker.load_skill", lambda _: spec)

    configure(5)
    return configure


def emit(facade):
    return facade.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "文本"},
            }
        )
    )["tasks"][0]


def success(*, turns):
    return EngineResult(
        engine_id="fake",
        turns=turns,
        receipt=Receipt(
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
    def __init__(self, kind="transient", *, reported=2):
        self.kind, self.reported = kind, reported
        self.requests = []

    async def health(self):
        return {"installed": True}

    async def run(self, request):
        self.requests.append(request)
        turns = min(self.reported, request.max_turns) if self.reported is not None else None
        return EngineResult(engine_id="fake", turns=turns, error="simulated", error_kind=self.kind)


async def no_sleep(_):
    pass


def once(facade, config_dir, engine):
    return asyncio.run(
        Worker(facade, config_dir, engines={"fake": engine}, sleeper=no_sleep).once()
    )


def charged(facade):
    with facade.engine.connect() as c:
        return c.execute(text("SELECT sum(turns_charged) FROM runs")).scalar_one()


def test_transient_retries_share_turn_allowance(configured, config_dir, allowance):
    emit(configured)
    engine = Engine()
    outcome = once(configured, config_dir, engine)
    assert outcome["reason"] == "task_turn_limit"
    assert [r.max_turns for r in engine.requests] == [4, 3, 1]
    assert charged(configured) == 5
    assert len(configured.list("effects")["items"]) == 1


def test_fresh_worker_does_not_reset_turns(configured, config_dir, allowance):
    emit(configured)
    engine = Engine(kind="fatal", reported=3)
    assert once(configured, config_dir, engine)["status"] == "queued"
    assert once(configured, config_dir, engine)["reason"] == "task_turn_limit"
    assert [r.max_turns for r in engine.requests] == [4, 2]
    assert charged(configured) == 5


def test_unknown_usage_charges_full_reserved_allowance(configured, config_dir, allowance):
    emit(configured)
    engine = Engine(kind="fatal", reported=None)
    assert once(configured, config_dir, engine)["status"] == "queued"
    assert charged(configured) == 4
    assert once(configured, config_dir, engine)["reason"] == "task_turn_limit"
    assert [r.max_turns for r in engine.requests] == [4, 1]
    assert charged(configured) == 5


def test_receipt_repair_receives_only_remaining_turns(configured, config_dir, allowance):
    allowance(3)
    emit(configured)

    class Repair(Engine):
        async def run(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return EngineResult(
                    engine_id="fake", error="bad receipt", error_kind="invalid_output", turns=2
                )
            return success(turns=1)

    engine = Repair()
    assert once(configured, config_dir, engine)["reason"] == "task_turn_limit"
    assert [r.max_turns for r in engine.requests] == [3, 1]
    assert charged(configured) == 3


def test_completed_verified_call_refunds_unused_reservation(configured, config_dir, allowance):
    emit(configured)

    class Good(Engine):
        async def run(self, request):
            return success(turns=1)

    assert once(configured, config_dir, Good())["status"] == "closed"
    with configured.engine.connect() as c:
        assert tuple(c.execute(text("SELECT turns_charged,turns_estimated FROM runs")).one()) == (
            1,
            0,
        )


def test_native_limit_violation_is_not_accepted_as_success(configured, config_dir, allowance):
    allowance(10)
    emit(configured)

    class Bad(Engine):
        async def run(self, request):
            return success(turns=request.max_turns + 1)

    assert once(configured, config_dir, Bad())["reason"] == "task_turn_limit"
    assert charged(configured) == 5


def test_cancelled_call_keeps_reservation_across_lease_recovery(configured, config_dir, allowance):
    allowance(4)
    task_id = emit(configured)

    async def scenario():
        started = asyncio.Event()

        class Blocking(Engine):
            async def run(self, request):
                started.set()
                await asyncio.Future()

        pending = asyncio.create_task(
            Worker(configured, config_dir, engines={"fake": Blocking()}).once()
        )
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
    assert charged(configured) == 4
    with transaction(configured.engine) as c:
        c.execute(
            text("UPDATE tasks SET lease_until=:past WHERE id=:id"),
            {
                "id": task_id,
                "past": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
    engine = Engine()
    assert once(configured, config_dir, engine)["reason"] == "task_turn_limit"
    assert engine.requests == []


def test_unknown_legacy_usage_requires_owner(configured, config_dir, allowance):
    emit(configured)
    engine = Engine(kind="fatal", reported=1)
    assert once(configured, config_dir, engine)["status"] == "queued"
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE runs SET turns_unknown=1"))
    assert once(configured, config_dir, engine)["reason"] == "task_turn_usage_unknown"
    assert len(engine.requests) == 1


def test_failover_uses_remaining_shared_turn_allowance(configured, config_dir, allowance):
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {
                    name: {"adapter": "codex_cli", "command": [sys.executable]}
                    for name in ("fake", "second")
                },
                "roles": {"dev": ["fake", "second"]},
            }
        },
    )
    configured.config = load(config_dir)
    emit(configured)
    first, second = Engine(kind="quota", reported=3), Engine(kind="quota", reported=2)
    outcome = asyncio.run(
        Worker(configured, config_dir, engines={"fake": first, "second": second}).once()
    )
    assert outcome["reason"] == "task_turn_limit"
    assert first.requests[0].max_turns == 4 and second.requests[0].max_turns == 2
    assert charged(configured) == 5


def test_upgrade_marks_preexisting_run_usage_unknown(tmp_path):
    engine = make_engine(tmp_path / "legacy.sqlite3")
    try:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        with engine.begin() as c:
            config.attributes["connection"] = c
            command.upgrade(config, "0004_engine_reservations")
        with transaction(engine) as c:
            c.execute(text("INSERT INTO tasks(id,status,priority) VALUES('task','queued','P2')"))
            c.execute(text("INSERT INTO runs(id,task_id,attempt) VALUES('run','task',1)"))
        migrate(engine)
        with engine.connect() as c:
            unknown = c.execute(text("SELECT turns_unknown FROM runs WHERE id='run'")).scalar_one()
            assert unknown == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("adapter", [HermesCLI, WorkBuddyCLI])
def test_native_cli_receives_remaining_limit(tmp_path, adapter):
    engine = adapter(
        "test", EngineConfig(adapter=adapter.adapter, command=[sys.executable], max_turns=4)
    )
    command, _, env = engine.command(
        EngineRequest(
            task_id="t",
            prompt="text",
            output_dir=tmp_path,
            max_turns=2,
        )
    )
    if adapter is HermesCLI:
        assert env["HERMES_MAX_ITERATIONS"] == "2"
        assert command[command.index("--max-turns") + 1] == "2"
    else:
        assert command[command.index("--max-turns") + 1] == "2"


def test_failure_still_reports_workbuddy_usage(tmp_path, monkeypatch):
    async def process(*args, **kwargs):
        return (
            1,
            json.dumps({"num_turns": 2, "usage": {"input_tokens": 10, "output_tokens": 3}}),
            "connection timeout",
        )

    monkeypatch.setattr("pa.engines.local.run_process", process)
    engine = WorkBuddyCLI("wb", EngineConfig(adapter="workbuddy_cli", command=[sys.executable]))
    result = asyncio.run(engine.run(EngineRequest(task_id="t", prompt="text", output_dir=tmp_path)))
    assert result.error_kind == "transient" and result.turns == 2
    assert result.usage.input_tokens == 10 and result.usage.output_tokens == 3


def test_hermes_usage_file_reports_api_iterations(tmp_path, monkeypatch):
    async def process(*args, **kwargs):
        (tmp_path / "usage.json").write_text(json.dumps({"api_calls": 3, "input_tokens": 8}))
        return 1, "", "connection timeout"

    monkeypatch.setattr("pa.engines.local.run_process", process)
    engine = HermesCLI("h", EngineConfig(adapter="hermes_cli", command=[sys.executable]))
    result = asyncio.run(engine.run(EngineRequest(task_id="t", prompt="text", output_dir=tmp_path)))
    assert result.turns == 3 and result.usage.input_tokens == 8


def test_codex_user_turn_is_not_fabricated_as_model_iterations(tmp_path):
    engine = CodexCLI("c", EngineConfig(adapter="codex_cli", command=[sys.executable]))
    counters = engine.counters(
        "\n".join(
            [
                json.dumps(
                    {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 1}}
                ),
                json.dumps(
                    {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 4}}
                ),
            ]
        ),
        EngineRequest(task_id="t", prompt="text", output_dir=tmp_path),
    )
    assert counters == {"input_tokens": 5, "output_tokens": 5}
    assert engine.reported_turns(counters) is None
