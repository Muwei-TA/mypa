import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from pa.common import new_id
from pa.config.loader import load
from pa.config.setup import save_files
from pa.engines.base import EngineResult
from pa.engines.selector import eligible
from pa.hub.dispatcher import claim
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.loader import SkillEngineRef, load_skill
from pa.skills.receipt import Receipt, Usage
from pa.store.db import transaction


class Engine:
    def __init__(self, *, error=None, health=None):
        self.error = error
        self.health_value = health or {"installed": True}
        self.calls = 0

    async def health(self):
        return self.health_value

    async def run(self, request):
        self.calls += 1
        if self.error:
            return EngineResult(engine_id="fake", error="simulated", error_kind=self.error)
        return EngineResult(
            engine_id="fake",
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


def setup_engines(facade, config_dir, *, roles=None, disabled=()):
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {
                    name: {
                        "adapter": "codex_cli",
                        "command": [sys.executable],
                        "enabled": name not in disabled,
                    }
                    for name in ("first", "second", "other")
                },
                "roles": roles or {"dev": ["first", "second"]},
            }
        },
    )
    facade.config = load(config_dir)


def emit(facade):
    return facade.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "文本"},
            }
        )
    )["tasks"][0]


def skill_patch(monkeypatch, *, refs=None, tiers=None):
    skill = load_skill().model_copy(deep=True)
    if refs is not None:
        skill.engines = [SkillEngineRef(**ref) for ref in refs]
    if tiers is not None:
        skill.allowed_tiers = tiers
    monkeypatch.setattr("pa.procs.worker.load_skill", lambda _: skill)
    return skill


def once(facade, config_dir, engines, preferred=None):
    return asyncio.run(Worker(facade, config_dir, engines=engines).once(preferred))


def occupy(facade, engine_id):
    with transaction(facade.engine) as c:
        c.execute(
            text("""
            INSERT INTO tasks(id,skill_id,status,priority,mandate_json,lease_owner,lease_engine_id)
            VALUES(:id,'other-skill','running','P2',:mandate,'other-worker',:engine)
        """),
            {"id": new_id(), "mandate": json.dumps({"strength": "inferred"}), "engine": engine_id},
        )


def test_skill_direct_ref_overrides_unrelated_global_dev(facade, config_dir, monkeypatch):
    setup_engines(facade, config_dir)
    skill_patch(monkeypatch, refs=[{"ref": "other"}])
    emit(facade)
    engines = {name: Engine() for name in ("first", "second", "other")}
    assert once(facade, config_dir, engines)["engine"] == "other"
    assert engines["first"].calls == engines["second"].calls == 0


def test_skill_role_order_is_used(facade, config_dir, monkeypatch):
    setup_engines(facade, config_dir, roles={"dev": ["first"], "ops": ["second"]})
    skill_patch(monkeypatch, refs=[{"role": "ops"}, {"role": "dev"}])
    emit(facade)
    assert once(facade, config_dir, {"first": Engine(), "second": Engine()})["engine"] == "second"


def test_rule_role_selects_its_configured_engines(facade, config_dir):
    setup_engines(facade, config_dir, roles={"dev": ["first"], "ops": ["other"]})
    rules = facade.config.rules.model_dump(mode="json", by_alias=True)
    rules["rules"][0]["engine_role"] = "ops"
    save_files(config_dir, {"rules.yaml": rules})
    facade.config = load(config_dir)
    emit(facade)
    assert once(facade, config_dir, {"first": Engine(), "other": Engine()})["engine"] == "other"


def test_disabled_injected_engine_is_not_selected(facade, config_dir):
    setup_engines(facade, config_dir, disabled=["first"])
    emit(facade)
    first, second = Engine(), Engine()
    assert once(facade, config_dir, {"first": first, "second": second})["engine"] == "second"
    assert first.calls == 0


def test_disallowed_tier_leaves_task_queued(facade, config_dir, monkeypatch):
    setup_engines(facade, config_dir)
    skill_patch(monkeypatch, tiers=["B"])
    task_id = emit(facade)
    engine = Engine()
    assert once(facade, config_dir, {"first": engine}) is None
    assert facade.show("tasks", task_id)["status"] == "queued" and engine.calls == 0


def test_preferred_engine_cannot_bypass_skill(facade, config_dir, monkeypatch):
    setup_engines(facade, config_dir)
    skill_patch(monkeypatch, refs=[{"ref": "second"}])
    emit(facade)
    engine = Engine()
    assert once(facade, config_dir, {"other": engine}, preferred="other") is None
    assert engine.calls == 0


@pytest.mark.parametrize(
    "health",
    [
        {"installed": False},
        {"installed": True, "enabled": False},
        {"installed": True, "healthy": False},
        {"installed": True, "authentication": "required"},
    ],
)
def test_unhealthy_first_engine_falls_through(facade, config_dir, health):
    setup_engines(facade, config_dir)
    emit(facade)
    first = Engine(health=health)
    assert once(facade, config_dir, {"first": first, "second": Engine()})["engine"] == "second"
    assert first.calls == 0


def test_reserved_slot_without_run_blocks_engine_and_uses_next(facade, config_dir):
    setup_engines(facade, config_dir)
    occupy(facade, "first")
    emit(facade)
    first = Engine()
    assert once(facade, config_dir, {"first": first, "second": Engine()})["engine"] == "second"
    assert first.calls == 0


def test_open_circuit_is_not_selected(facade, config_dir):
    setup_engines(facade, config_dir)
    emit(facade)
    with transaction(facade.engine) as c:
        c.execute(text("INSERT INTO engine_health(engine_id,breaker_state) VALUES('first','open')"))
    first = Engine()
    assert once(facade, config_dir, {"first": first, "second": Engine()})["engine"] == "second"
    assert first.calls == 0


def test_failover_does_not_escape_skill_allowance(facade, config_dir, monkeypatch):
    setup_engines(facade, config_dir)
    skill_patch(monkeypatch, refs=[{"ref": "first"}])
    emit(facade)
    first, second = Engine(error="quota"), Engine()
    assert once(facade, config_dir, {"first": first, "second": second})["status"] == "queued"
    assert first.calls == 1 and second.calls == 0


def test_failover_skips_saturated_engine(facade, config_dir):
    setup_engines(facade, config_dir)
    occupy(facade, "second")
    emit(facade)
    first, second = Engine(error="quota"), Engine()
    assert once(facade, config_dir, {"first": first, "second": second})["status"] == "queued"
    assert first.calls == 1 and second.calls == 0


def test_engine_slot_is_reserved_atomically_before_run_exists(facade, config_dir):
    setup_engines(facade, config_dir)
    tasks = [emit(facade) for _ in range(4)]
    skill = load_skill().model_copy(update={"concurrency": 4})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda task_id: claim(
                    facade.engine, facade.config, "first", task_id=task_id, skill=skill
                ),
                tasks,
            )
        )
    assert sum(row is not None for row in results) == 1
    assert next(row for row in results if row is not None)["lease_engine_id"] == "first"
    assert facade.list("tasks")["items"]  # No engine run was needed to reserve the slot.


def test_idle_worker_needs_no_engine(facade, config_dir):
    assert once(facade, config_dir, {}) is None


def test_monetary_budget_gate_can_filter_candidates(facade, config_dir):
    setup_engines(facade, config_dir)

    async def select():
        return [
            name
            async for _, name in eligible(
                facade.engine,
                facade.config,
                load_skill(),
                injected={"first": Engine(), "second": Engine()},
                budget_allows=lambda name: name == "second",
            )
        ]

    assert asyncio.run(select()) == ["second"]
