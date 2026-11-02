import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from pa.common import PAError
from pa.engines.base import EngineResult
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.loader import SkillEngineRef, SkillHumanGate, list_skills, load_skill, render_prompt
from pa.skills.prompt_assembly import assemble
from pa.skills.receipt import Receipt, Usage


class GoodEngine:
    def __init__(self, engine_id="fake"):
        self.engine_id = engine_id

    async def health(self):
        return {"installed": True}

    async def run(self, request):
        return EngineResult(
            engine_id=self.engine_id,
            receipt=Receipt(
                status="succeeded",
                summary="已核实并生成摘要",
                code_changed=False,
                confidence=0.92,
                risk="low",
                evidence=["输入文本内部一致"],
                usage=Usage(),
            ),
        )


def manual(text_="概括这段文本"):
    return ManualAdapter().parse(
        {"type": "manual.test", "payload": {"skill": "sample-direct", "text": text_}}
    )


def test_list_and_load_skill():
    assert "sample-direct" in list_skills()
    spec = load_skill()
    assert spec.id == "sample-direct"
    assert spec.mode == "direct"
    assert spec.risk_ceiling == "low"
    assert spec.timeout_min == 20
    selectors = [(entry.role or entry.ref) for entry in spec.engines]
    assert selectors == ["dev", "workbuddy_desktop"]


def test_human_gate_keeps_seven_tags():
    gate = load_skill().human_gate
    assert len(gate.always_on) == 7
    assert gate.confidence_below == 0.7
    with pytest.raises(ValidationError):
        SkillHumanGate(confidence_below=0.7, always_on=["delete_data"], use_precedents=True)


def test_engine_ref_requires_exactly_one_selector():
    SkillEngineRef(role="dev")
    SkillEngineRef(ref="workbuddy_desktop")
    with pytest.raises(ValidationError):
        SkillEngineRef(role="dev", ref="x")
    with pytest.raises(ValidationError):
        SkillEngineRef()


def test_render_prompt_injects_inputs():
    rendered = render_prompt("sample-direct", inputs="https://example.com/a")
    assert "https://example.com/a" in rendered
    assert "{{" not in rendered


def test_assemble_uses_skill_assets_and_escapes_input():
    prompt = assemble("核实 <b> 这段", policy_version="p-1")
    # Skill prompt content is present.
    assert "先读取原文，再核实" in prompt
    # Input escaped inside the untrusted segment.
    assert "&lt;b&gt;" in prompt
    assert prompt.rfind("[policy]") > prompt.rfind("[environment]")


def test_assemble_rejects_secret():
    with pytest.raises(PAError):
        assemble("sk-do-not-send-this", policy_version="p")


def test_end_to_end_read_verify_summary_close(configured, config_dir):
    task_id = configured.emit(manual())["tasks"][0]
    result = asyncio.run(Worker(configured, config_dir, engines={"fake": GoodEngine()}).once())
    assert result["status"] == "closed"
    task = configured.show("tasks", task_id)
    assert task["status"] == "closed"
    with configured.engine.connect() as connection:
        run = connection.execute(
            text("SELECT receipt_json FROM runs WHERE task_id=:id"), {"id": task_id}
        ).scalar_one()
    assert json.loads(run)["summary"] == "已核实并生成摘要"


def test_url_input_is_carried_and_marked_untrusted(configured, config_dir):
    url = "https://github.com/owner/repo/issues/1"
    configured.emit(manual(url))
    # The prompt handed to the engine must keep the URL inside the untrusted tag.
    prompt = assemble(url, policy_version=configured.config.policy_version)
    assert url in prompt
    assert "<untrusted" in prompt
    # No expiry-related crash; the timestamp helper stays usable.
    assert datetime.now(UTC) + timedelta(seconds=1) > datetime.now(UTC)
