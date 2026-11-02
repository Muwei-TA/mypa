"""pa-gui process model (M4-03, PRD §3.3): single GUI lock, C-tier only.

The real pa-gui LaunchAgent runs in the graphical session on the Mac mini
(deferred). This module implements the lock discipline it must follow:
one GUI task globally, only C-tier claims, notifications paused while a
task runs, and pausing when the owner is using keyboard/mouse.
"""

import asyncio
import time

from pa.common import PAError


class GUILock:
    """Process-wide single-slot lock (§8.4 mutual exclusion)."""

    def __init__(self):
        self._held = False

    def acquire(self):
        if self._held:
            raise PAError("another GUI task is running (global GUI lock)", "conflict", 409)
        self._held = True
        return True

    def release(self):
        self._held = False

    @property
    def held(self):
        return self._held


class NotificationPauser:
    """Runs body with system notifications suppressed (§8.4 anti-disturb)."""

    def __init__(self):
        self.paused = False

    async def __aenter__(self):
        self.paused = True
        return self

    async def __aexit__(self, *exc):
        self.paused = False
        return False


class GUIRunner:
    """Claims C-tier tasks under the GUI lock and runs playbooks."""

    def __init__(self, *, lock: GUILock | None = None, driver=None, max_user_wait_s: float = 300):
        self.lock = lock or GUILock()
        self.driver = driver
        self.max_user_wait_s = max_user_wait_s

    def claim(self, task) -> dict:
        """Claim one task if it is C-tier and the lock is free."""
        if task.get("tier") != "C":
            raise PAError("pa-gui only claims C-tier tasks", "forbidden", 3)
        self.lock.acquire()
        return {"claimed": task["id"], "tier": "C"}

    async def run(self, task, run_playbook) -> dict:
        """Wait for owner idle, then execute under the lock with notifications paused."""
        if not self.lock.held:
            self.claim(task)
        waited = 0.0
        while self.driver is not None and self.driver.user_active():
            if waited >= self.max_user_wait_s:
                self.lock.release()
                return {"status": "failed", "error_kind": "owner_busy_timeout"}
            await asyncio.sleep(5)
            waited += 5
        started = time.monotonic()
        async with NotificationPauser():
            result = run_playbook()
        duration = round(time.monotonic() - started, 3)
        self.lock.release()
        return {**result, "duration_s": duration}


def gui_slots() -> int:
    """Fixed slot count: the global GUI mutex allows exactly one (§3.3)."""
    return 1


def is_c_tier(engine_config: dict) -> bool:
    """An engine is C-tier when its adapter is gui."""
    return engine_config.get("adapter") == "gui"
