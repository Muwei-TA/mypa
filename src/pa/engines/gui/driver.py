"""GUI automation driver contract (M4-01, PRD §8.4).

The driver abstracts the macOS-only machinery (AX API, AppleScript, Quartz
events, Vision OCR) behind a small interface so the playbook executor and
tests can run on any platform. The real macOS backend ships with pa-gui on
the Mac mini; a synthetic backend drives unit tests here.

Clipboard handling must snapshot and restore the previous pasteboard around
paste_text steps (§8.4).
"""

from dataclasses import dataclass, field


@dataclass
class Element:
    role: str | None = None
    title: str | None = None
    value: str | None = None
    focused: bool = False


@dataclass
class StepOutcome:
    ok: bool
    detail: str = ""
    screenshot: str | None = None
    extras: dict = field(default_factory=dict)


class GUIDriver:
    """Platform backend contract; one instance per GUI task run."""

    available: bool = False

    def activate_app(self, app: str) -> StepOutcome:
        raise NotImplementedError

    def press_hotkey(self, combo: str) -> StepOutcome:
        raise NotImplementedError

    def wait_for(
        self,
        *,
        role: str | None = None,
        title: str | None = None,
        title_absent: str | None = None,
        timeout_s: float,
    ) -> StepOutcome:
        raise NotImplementedError

    def paste_text(self, text: str) -> StepOutcome:
        """Paste via clipboard; snapshot and restore the previous content."""
        raise NotImplementedError

    def read_element(self, *, role: str | None = None, title: str | None = None) -> StepOutcome:
        raise NotImplementedError

    def screenshot(self, save_as: str) -> StepOutcome:
        raise NotImplementedError

    def user_active(self) -> bool:
        """True when the owner is currently using keyboard/mouse (anti-fight)."""
        raise NotImplementedError


class SyntheticDriver(GUIDriver):
    """In-memory backend for tests: scripted outcomes, no OS calls."""

    available = True

    def __init__(self, script: list[StepOutcome] | None = None, *, user_busy=False):
        self.script = list(script or [])
        self.calls: list[tuple] = []
        self.clipboard_history: list[str | None] = []
        self._clipboard: str | None = None
        self.user_busy = user_busy

    def _next(self, fallback: StepOutcome) -> StepOutcome:
        self.calls.append(("pop",))
        return self.script.pop(0) if self.script else fallback

    def activate_app(self, app: str) -> StepOutcome:
        self.calls.append(("activate_app", app))
        return self._next(StepOutcome(ok=True, detail=f"activated {app}"))

    def press_hotkey(self, combo: str) -> StepOutcome:
        self.calls.append(("press_hotkey", combo))
        return self._next(StepOutcome(ok=True, detail=f"pressed {combo}"))

    def wait_for(self, *, role=None, title=None, title_absent=None, timeout_s=10) -> StepOutcome:
        self.calls.append(("wait_for", role, title, title_absent, timeout_s))
        return self._next(StepOutcome(ok=True, detail="condition met"))

    def paste_text(self, text: str) -> StepOutcome:
        # §8.4: snapshot previous clipboard, paste, then restore afterwards.
        self.clipboard_history.append(self._clipboard)
        self._clipboard = text
        self.calls.append(("paste_text", text))
        outcome = self._next(StepOutcome(ok=True, detail="pasted"))
        self._clipboard = self.clipboard_history.pop() if self.clipboard_history else None
        return outcome

    def read_element(self, *, role=None, title=None) -> StepOutcome:
        self.calls.append(("read_element", role, title))
        value = f"synthetic reply for {title or role}"
        return self._next(StepOutcome(ok=True, detail=value, extras={"text": value}))

    def screenshot(self, save_as: str) -> StepOutcome:
        self.calls.append(("screenshot", save_as))
        return self._next(StepOutcome(ok=True, detail=f"saved {save_as}"))

    def user_active(self) -> bool:
        return self.user_busy
