"""GUI engine adapter wired into the pa.engines plugin point (M4-01/02).

Registers as adapter "gui": loads the engine's playbook, enforces C-tier
hard limits, runs steps via the platform driver and converts the raw reply.
The macOS driver itself (pyobjc/AppleScript/Quartz/Vision) ships with the
Mac mini deployment; on other platforms health() reports unavailable so
the selector never picks a gui engine for real dispatch.
"""

from pathlib import Path

from pa.engines.base import EngineRequest, EngineResult
from pa.engines.gui.driver import GUIDriver
from pa.engines.gui.limits import check_c_tier_limits
from pa.engines.gui.playbook import Playbook, PlaybookRun
from pa.skills.receipt import Receipt, Usage


class GuiEngine:
    adapter = "gui"

    def __init__(self, engine_id: str, config):
        self.id = engine_id
        self.config = config
        self.playbook: Playbook | None = None
        self.driver = None  # tests inject a driver; production uses the macOS backend

    def _load_playbook(self) -> Playbook:
        if self.playbook is None:
            ref = getattr(self.config, "playbook", None)
            if not ref:
                from pa.common import PAError

                raise PAError("engine has no playbook", "validation_error", 2)
            self.playbook = Playbook.load(Path(ref))
        return self.playbook

    async def health(self):
        driver = self.driver or GUIDriver()
        app_path = getattr(self.config, "app_path", None)
        # Real dispatch requires the macOS backend and a configured app.
        return {
            "installed": bool(driver.available and app_path),
            "enabled": self.config.enabled,
            "reason": None if driver.available else "macOS GUI backend not available",
        }

    async def run(self, request: EngineRequest, *, skill=None) -> EngineResult:
        playbook = self._load_playbook()
        check_c_tier_limits(
            skill=skill,
            tools=getattr(skill, "tools", None) if skill else None,
        )
        driver = self.driver or GUIDriver()
        if not driver.available:
            return EngineResult(
                engine_id=self.id,
                error="macOS GUI driver is not configured",
                error_kind="unavailable",
            )
        run = PlaybookRun(playbook, driver, screenshot_dir=request.output_dir / "shots")
        result = run.execute(prompt=request.prompt)
        if result["status"] != "succeeded":
            return EngineResult(
                engine_id=self.id,
                error=result.get("detail") or "playbook failed",
                error_kind=result.get("error_kind", "ui_drift"),
            )
        return EngineResult(
            engine_id=self.id,
            turns=1,
            receipt=Receipt(
                status="needs_human",
                summary=str(result.get("reply") or "GUI output missing"),
                code_changed=False,
                confidence=0.6,
                risk="low",
                evidence=[],
                usage=Usage(),
            ),
        )
