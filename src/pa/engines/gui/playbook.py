"""Playbook parsing and execution (M4-02, PRD §8.4).

A playbook is a YAML file: app, optional version_hint, steps, result
extractor. The executor walks steps in order, enforcing pre/post conditions
through the driver; any failed step aborts with error_kind=ui_drift so the
caller (engine + breaker) can treat it as an engine fault, not a task fault.

Step types (§8.4 example): activate_app, new_session, wait_for, paste_text,
press, wait_until_idle, copy_last_reply, screenshot.
"""

from pathlib import Path

import yaml
from pydantic import Field

from pa.common import PAError
from pa.config.schema import Model
from pa.engines.gui.driver import SyntheticDriver


class PlaybookStep(Model):
    kind: str
    params: dict = Field(default_factory=dict)


class Playbook(Model):
    app: str
    version_hint: str | None = None
    steps: list[PlaybookStep] = Field(default_factory=list)
    result: dict = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Playbook":
        data = yaml.safe_load(path.read_text()) or {}
        steps = []
        for raw in data.get("steps", []):
            (kind, params), = raw.items()
            steps.append(PlaybookStep(kind=kind, params=dict(params or {})))
        return cls(
            app=data["app"],
            version_hint=data.get("version_hint"),
            steps=steps,
            result=data.get("result") or {},
        )


SUPPORTED_STEPS = frozenset(
    {
        "activate_app",
        "new_session",
        "wait_for",
        "paste_text",
        "press",
        "wait_until_idle",
        "copy_last_reply",
        "screenshot",
    }
)


class PlaybookRun:
    """One execution of a playbook against a driver."""

    def __init__(self, playbook: Playbook, driver=None, *, screenshot_dir: Path | None = None):
        self.playbook = playbook
        self.driver = driver or SyntheticDriver()
        self.screenshot_dir = screenshot_dir
        self.log: list[dict] = []

    def _record(self, step: PlaybookStep, outcome) -> None:
        self.log.append(
            {
                "step": step.kind,
                "ok": outcome.ok,
                "detail": outcome.detail,
                "screenshot": outcome.screenshot,
            }
        )

    def _fail(self, step: PlaybookStep, detail: str):
        outcome = type("Outcome", (), {"ok": False, "detail": detail, "screenshot": None})()
        self._record(step, outcome)
        return {"status": "failed", "error_kind": "ui_drift", "detail": detail, "log": self.log}

    def _check_preconditions(self, step: PlaybookStep) -> str | None:
        """Pre-condition: driver available; user not fighting the mouse (§8.4)."""
        if not getattr(self.driver, "available", False):
            return "gui driver unavailable"
        if step.kind in SUPPORTED_STEPS and self.driver.user_active():
            return "owner is actively using keyboard/mouse"
        return None

    def execute(self, *, prompt: str = "", dry_run: bool = False) -> dict:
        for step in self.playbook.steps:
            if step.kind not in SUPPORTED_STEPS:
                return self._fail(step, f"unknown step type: {step.kind}")
            problem = self._check_preconditions(step)
            if problem:
                return self._fail(step, problem)
            handler = getattr(self, "_do_" + step.kind)
            outcome = handler(step, prompt, dry_run=dry_run)
            self._record(step, outcome)
            if not outcome.ok:
                return {
                    "status": "failed",
                    "error_kind": "ui_drift",
                    "detail": outcome.detail,
                    "log": self.log,
                }
        return {"status": "succeeded", "reply": self._last_reply, "log": self.log}

    _last_reply: str | None = None

    def _do_activate_app(self, step, prompt, *, dry_run=False):
        return self.driver.activate_app(step.params.get("app") or self.playbook.app)

    def _do_new_session(self, step, prompt, *, dry_run=False):
        combo = step.params.get("hotkey", "cmd+n")
        return self.driver.press_hotkey(combo)

    def _do_wait_for(self, step, prompt, *, dry_run=False):
        return self.driver.wait_for(
            role=step.params.get("ax_role"),
            title=step.params.get("ax_title"),
            title_absent=step.params.get("ax_title_absent"),
            timeout_s=step.params.get("timeout_s", 10),
        )

    def _do_paste_text(self, step, prompt, *, dry_run=False):
        source = step.params.get("from", "prompt")
        text = prompt if source == "prompt" else str(step.params.get("text", ""))
        return self.driver.paste_text(text)

    def _do_press(self, step, prompt, *, dry_run=False):
        return self.driver.press_hotkey(step.params["key"])

    def _do_wait_until_idle(self, step, prompt, *, dry_run=False):
        indicator = step.params.get("indicator", {})
        return self.driver.wait_for(
            title_absent=indicator.get("ax_title_absent"),
            timeout_s=step.params.get("timeout_s", 600),
        )

    def _do_copy_last_reply(self, step, prompt, *, dry_run=False):
        outcome = self.driver.read_element(
            role=step.params.get("ax_role", "AXTextArea"),
            title=step.params.get("ax_title"),
        )
        if outcome.ok:
            self._last_reply = outcome.extras.get("text", outcome.detail)
        return outcome

    def _do_screenshot(self, step, prompt, *, dry_run=False):
        name = step.params.get("save_as", "final")
        outcome = self.driver.screenshot(name)
        if outcome.ok and self.screenshot_dir is not None:
            outcome.screenshot = str(self.screenshot_dir / f"{name}.png")
        return outcome


def load_playbook(config_dir: Path, engine_id: str) -> Playbook:
    """Resolve the playbook path from engines.yaml for one GUI engine."""
    import re

    from pa.config.loader import load

    config = load(config_dir)
    entry = config.engines.engines.get(engine_id)
    if entry is None:
        raise PAError("engine not found", "not_found", 4)
    playbook_ref = getattr(entry, "playbook", None)
    if not playbook_ref:
        raise PAError("engine has no playbook", "validation_error", 2)
    path = config_dir / playbook_ref
    if not path.exists():
        raise PAError(f"playbook file missing: {playbook_ref}", "not_found", 4)
    if not re.match(r"^[\w./-]+$", playbook_ref):
        # sanity: file name must be a plain relative path, no traversal
        raise PAError("invalid playbook path", "validation_error", 2)
    return Playbook.load(path)
