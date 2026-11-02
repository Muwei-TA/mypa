import asyncio
import sys

from sqlalchemy import text

from pa.config.loader import load
from pa.config.setup import save_files
from pa.engines.base import EngineResult
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.receipt import Receipt, Usage


def receipt(**values):
    return Receipt(
        status="succeeded",
        summary="测试结论",
        code_changed=False,
        confidence=0.9,
        risk="low",
        evidence=["输入文本"],
        usage=Usage(),
        **values,
    )


def manual():
    return ManualAdapter().parse(
        {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "概括这段文本"}}
    )


async def no_sleep(_):
    return None


class ScriptedEngine:
    def __init__(self, engine_id, results):
        self.id = engine_id
        self.results = list(results)
        self.calls = 0

    async def health(self):
        return {"installed": True}

    async def run(self, request):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


def failure(kind, engine_id="fake"):
    return EngineResult(engine_id=engine_id, error="boom", error_kind=kind)


def test_transient_retries_then_success_without_retry_count(configured, config_dir):
    task_id = configured.emit(manual())["tasks"][0]
    engine = ScriptedEngine(
        "fake",
        [
            failure("transient"),
            failure("transient"),
            failure("transient"),
            EngineResult(engine_id="fake", receipt=receipt()),
        ],
    )
    result = asyncio.run(
        Worker(configured, config_dir, engines={"fake": engine}, sleeper=no_sleep).once()
    )
    assert result["status"] == "closed"
    assert configured.show("tasks", task_id)["retry_count"] == 0
    with configured.engine.connect() as connection:
        runs = connection.execute(
            text("SELECT count(*) FROM runs WHERE task_id=:id"), {"id": task_id}
        ).scalar_one()
    assert runs == 4
    assert engine.calls == 4


def test_transient_exhausted_requeues_with_engine_hint(configured, config_dir):
    task_id = configured.emit(manual())["tasks"][0]
    engine = ScriptedEngine("fake", [failure("transient")])
    result = asyncio.run(
        Worker(configured, config_dir, engines={"fake": engine}, sleeper=no_sleep).once()
    )
    assert result == {
        "task_id": task_id,
        "status": "queued",
        "retry_count": 1,
        "engine_hint": "fake",
    }
    assert engine.calls == 4  # three transient retries plus the initial attempt


def test_retry_exhausted_awaits_decision(configured, config_dir):
    task_id = configured.emit(manual())["tasks"][0]
    worker = Worker(
        configured,
        config_dir,
        engines={"fake": ScriptedEngine("fake", [failure("transient")])},
        sleeper=no_sleep,
    )
    first = asyncio.run(worker.once())
    second = asyncio.run(worker.once())
    assert first["retry_count"] == 1 and second["retry_count"] == 2
    third = asyncio.run(worker.once())
    assert third["status"] == "awaiting_decision"
    assert third["reason"] == "retry_exhausted"
    with configured.engine.connect() as connection:
        decisions = connection.execute(
            text("SELECT count(*) FROM decisions WHERE task_id=:id"), {"id": task_id}
        ).scalar_one()
        reason = connection.execute(
            text(
                "SELECT reason_json FROM task_events WHERE task_id=:id ORDER BY rowid DESC LIMIT 1"
            ),
            {"id": task_id},
        ).scalar_one()
    assert decisions == 1
    assert "retry_exhausted" in reason


def _two_engine_fixture(facade, config_dir):
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {
                    "e1": {"adapter": "codex_cli", "command": [sys.executable]},
                    "e2": {"adapter": "codex_cli", "command": [sys.executable]},
                },
                "roles": {"dev": ["e1", "e2"]},
            }
        },
    )
    facade.config = load(config_dir)
    return facade


def test_auth_and_quota_switch_engine(facade, config_dir):
    _two_engine_fixture(facade, config_dir)
    for kind in ("auth", "quota"):
        facade.emit(manual())
        e1 = ScriptedEngine("e1", [failure(kind, "e1")])
        e2 = ScriptedEngine("e2", [EngineResult(engine_id="e2", receipt=receipt())])
        result = asyncio.run(
            Worker(facade, config_dir, engines={"e1": e1, "e2": e2}, sleeper=no_sleep).once()
        )
        assert result["status"] == "closed"
        assert result["engine"] == "e2"
        assert e1.calls == 1 and e2.calls == 1


def test_invalid_output_repairs_then_switches_engine(facade, config_dir):
    _two_engine_fixture(facade, config_dir)
    facade.emit(manual())
    e1 = ScriptedEngine("e1", [failure("invalid_output", "e1")])
    e2 = ScriptedEngine("e2", [EngineResult(engine_id="e2", receipt=receipt())])
    result = asyncio.run(
        Worker(facade, config_dir, engines={"e1": e1, "e2": e2}, sleeper=no_sleep).once()
    )
    assert result["engine"] == "e2"
    assert e1.calls == 2  # initial attempt plus the same-engine repair
    assert e2.calls == 1


def test_redispatch_deprioritizes_last_engine(facade, config_dir):
    _two_engine_fixture(facade, config_dir)
    facade.emit(manual())
    e1 = ScriptedEngine("e1", [failure("quota", "e1")])
    e2 = ScriptedEngine("e2", [failure("quota", "e2")])
    worker = Worker(facade, config_dir, engines={"e1": e1, "e2": e2}, sleeper=no_sleep)

    first = asyncio.run(worker.once())
    assert first["status"] == "queued"
    assert first["engine_hint"] == "e2"  # last engine used gets excluded next time

    # Now e1 succeeds; the hint on e2 must make the worker choose e1 first.
    e1.results.append(EngineResult(engine_id="e1", receipt=receipt()))
    second = asyncio.run(worker.once())
    assert second["engine"] == "e1"
    assert second["status"] == "closed"
