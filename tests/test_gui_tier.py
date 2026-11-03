"""M4 GUI tier: driver, playbook executor, C-tier limits, conversion, pa-gui model, self-check."""

import asyncio
import time
from pathlib import Path

import pytest

from pa.common import PAError
from pa.engines.base import EngineRequest
from pa.engines.gui.convert import build_prompt, convert, parse_model_output
from pa.engines.gui.driver import StepOutcome, SyntheticDriver
from pa.engines.gui.engine import GuiEngine
from pa.engines.gui.limits import CONFIDENCE_CAP, check_c_tier_limits, clamp_confidence
from pa.engines.gui.playbook import Playbook, PlaybookRun
from pa.engines.gui.playbooks import (
    DOUBAO_DESKTOP,
    install_playbooks,
)
from pa.engines.gui.selfcheck import purge_old_screenshots, self_check, should_trip_breaker
from pa.procs.gui import GUILock, GUIRunner, gui_slots, is_c_tier


def _playbook_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "doubao_desktop.yaml"
    path.write_text(DOUBAO_DESKTOP)
    return path


def test_playbook_parses_all_step_types(tmp_path):
    playbook = Playbook.load(_playbook_yaml(tmp_path))
    assert playbook.app == "豆包"
    kinds = [s.kind for s in playbook.steps]
    assert kinds == [
        "activate_app",
        "new_session",
        "wait_for",
        "paste_text",
        "press",
        "wait_until_idle",
        "copy_last_reply",
        "screenshot",
    ]


def test_playbook_run_succeeds_and_restores_clipboard(tmp_path):
    playbook = Playbook.load(_playbook_yaml(tmp_path))
    driver = SyntheticDriver()
    run = PlaybookRun(playbook, driver)
    result = run.execute(prompt="帮我总结这段文本")
    assert result["status"] == "succeeded"
    assert result["reply"]
    # paste step snapshotted and restored the clipboard
    paste_calls = [c for c in driver.calls if c[0] == "paste_text"]
    assert len(paste_calls) == 1
    assert driver.clipboard_history == []


def test_playbook_fails_with_ui_drift_on_step_failure(tmp_path):
    playbook = Playbook.load(_playbook_yaml(tmp_path))
    script = [StepOutcome(ok=True)] * 3 + [StepOutcome(ok=False, detail="窗口未找到")]
    driver = SyntheticDriver(script=script)
    run = PlaybookRun(playbook, driver)
    result = run.execute(prompt="x")
    assert result["status"] == "failed"
    assert result["error_kind"] == "ui_drift"
    assert "窗口未找到" in result["detail"]


def test_playbook_rejects_unknown_step(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("app: X\nsteps:\n  - do_magic: {}\n")
    run = PlaybookRun(Playbook.load(path), SyntheticDriver())
    result = run.execute(prompt="")
    assert result["status"] == "failed"
    assert "unknown step" in result["detail"]


def test_playbook_pauses_when_owner_fights_input(tmp_path):
    playbook = Playbook.load(_playbook_yaml(tmp_path))
    driver = SyntheticDriver(user_busy=True)
    run = PlaybookRun(playbook, driver)
    result = run.execute(prompt="x")
    assert result["status"] == "failed"
    assert "keyboard/mouse" in result["detail"]


class _Skill:
    def __init__(self, **kwargs):
        self.risk_ceiling = kwargs.get("risk_ceiling", "low")
        self.repo_write = kwargs.get("repo_write", False)
        self.c_tier_auto_close = kwargs.get("c_tier_auto_close", False)
        self.tools = kwargs.get("tools", [])


def test_c_tier_hard_limits():
    limits = check_c_tier_limits(skill=_Skill(tools=["memory_search"]))
    assert limits["can_edit_code"] is False
    assert limits["confidence_cap"] == CONFIDENCE_CAP == 0.6
    assert limits["auto_close"] is False

    with pytest.raises(PAError):
        check_c_tier_limits(skill=_Skill(risk_ceiling="medium"))
    with pytest.raises(PAError):
        check_c_tier_limits(skill=_Skill(repo_write=True))
    with pytest.raises(PAError):
        check_c_tier_limits(skill=_Skill(tools=["spend_money"]))
    with pytest.raises(PAError):
        check_c_tier_limits(skill=_Skill(tools=["external_message"]))
    assert check_c_tier_limits(skill=_Skill(c_tier_auto_close=True))["auto_close"] is True


def test_confidence_clamped():
    assert clamp_confidence(0.9) == 0.6
    assert clamp_confidence(0.4) == 0.4
    assert clamp_confidence(None) is None


def test_structured_conversion_template_and_clamp():
    prompt = build_prompt("原始输出")
    assert "结构化转换器" in prompt
    assert "原始输出" in prompt

    ok = parse_model_output({"answer": "总结：完成", "confidence": 0.8})
    assert ok["ok"] and ok["confidence"] == 0.6 and ok["clamped"] is True

    assert parse_model_output({})["ok"] is False
    assert parse_model_output({"answer": "x", "confidence": "high"})["ok"] is False

    calls = []

    def model_call(prompt_text):
        calls.append(prompt_text)
        return {"answer": "结果", "confidence": 0.55}

    result = convert("原始", model_call)
    assert result["ok"] and result["confidence"] == 0.55
    assert len(calls) == 1  # exactly one cheap model call


def test_gui_lock_single_slot():
    lock = GUILock()
    assert gui_slots() == 1
    lock.acquire()
    with pytest.raises(PAError):
        lock.acquire()
    lock.release()
    lock.acquire()


def test_gui_runner_claims_c_tier_only():
    runner = GUIRunner(driver=SyntheticDriver())
    with pytest.raises(PAError):
        runner.claim({"id": "t-1", "tier": "A"})
    out = runner.claim({"id": "t-2", "tier": "C"})
    assert out["claimed"] == "t-2"


def test_gui_runner_waits_for_owner_idle():
    busy = SyntheticDriver(user_busy=True)
    busy.user_busy = False  # becomes idle after first check via monkey state
    runner = GUIRunner(driver=busy, max_user_wait_s=0)

    def playbook():
        return {"status": "succeeded"}

    out = asyncio.run(runner.run({"id": "t-3", "tier": "C"}, playbook))
    assert out["status"] == "succeeded"
    assert "duration_s" in out


def test_gui_runner_owner_busy_timeout():
    driver = SyntheticDriver(user_busy=True)
    runner = GUIRunner(driver=driver, max_user_wait_s=0)

    out = asyncio.run(runner.run({"id": "t-4", "tier": "C"}, lambda: {"status": "succeeded"}))
    assert out["status"] == "failed"
    assert out["error_kind"] == "owner_busy_timeout"


def test_is_c_tier():
    assert is_c_tier({"adapter": "gui"}) is True
    assert is_c_tier({"adapter": "codex_cli"}) is False


def test_screenshot_retention(tmp_path):
    now = time.time()
    fresh = tmp_path / "fresh.png"
    fresh.write_text("x")
    stale = tmp_path / "stale.png"
    stale.write_text("x")
    import os

    os.utime(stale, (now - 8 * 86400, now - 8 * 86400))
    report = purge_old_screenshots(tmp_path, now=now)
    assert report == {"removed": 1, "kept": 1}
    assert not stale.exists() and fresh.exists()


def test_self_check_and_breaker_trip():
    checks = self_check(
        {
            "doubao_desktop": lambda: {"status": "failed", "error_kind": "ui_drift"},
            "codex_desktop": lambda: {"status": "succeeded"},
        }
    )
    assert checks["engines"]["doubao_desktop"]["status"] == "failed"
    assert checks["engines"]["codex_desktop"]["status"] == "succeeded"

    assert should_trip_breaker(["succeeded", "failed", "failed"]) is False
    assert should_trip_breaker(["failed", "failed", "failed"]) is True


def test_three_desktop_playbooks_install_and_dry_run(tmp_path):
    written = install_playbooks(tmp_path)
    assert set(written) == {"codex_desktop", "doubao_desktop", "workbuddy_desktop"}
    for engine_id in ("codex_desktop", "doubao_desktop", "workbuddy_desktop"):
        playbook = Playbook.load(Path(written[engine_id]))
        run = PlaybookRun(playbook, SyntheticDriver())
        result = run.execute(prompt="只打开不提交", dry_run=True)
        assert result["status"] == "succeeded", engine_id


def test_gui_engine_adapter_health_and_run(tmp_path):
    written = install_playbooks(tmp_path)

    class _Entry:
        enabled = True
        app_path = "/Applications/Doubao.app"
        playbook = str(written["doubao_desktop"])

    engine = GuiEngine("doubao_desktop", _Entry())
    engine.driver = SyntheticDriver()
    health = asyncio.run(engine.health())
    assert health["installed"] is True

    request = EngineRequest(
        task_id="t-gui",
        prompt="总结一下",
        output_dir=tmp_path / "out",
    )
    result = asyncio.run(engine.run(request, skill=_Skill()))
    assert result.error is None
    assert result.engine_id == "doubao_desktop"

    # C-tier hard limits block code-editing skills
    with pytest.raises(PAError):
        asyncio.run(engine.run(request, skill=_Skill(repo_write=True)))


def test_cli_playbook_dry_run_and_record_placeholder(config_dir):
    from typer.testing import CliRunner

    from pa.cli.main import app
    from pa.config.setup import save_files

    install_playbooks(config_dir)
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {
                    "doubao_desktop": {
                        "adapter": "gui",
                        "enabled": True,
                        "app_path": "/Applications/Doubao.app",
                        "playbook": "playbooks/doubao_desktop.yaml",
                    }
                },
                "roles": {"browser": ["doubao_desktop"]},
            }
        },
    )
    runner = CliRunner()
    base = ["--config-dir", str(config_dir), "engine", "playbook"]
    dry = runner.invoke(app, base + ["dry-run", "doubao_desktop", "--json"])
    assert dry.exit_code == 2
    assert '"status": "failed"' in dry.output
    assert "driver unavailable" in dry.output

    record = runner.invoke(app, base + ["record", "doubao_desktop", "--json"])
    assert record.exit_code == 0
    assert '"recorded": false' in record.output
