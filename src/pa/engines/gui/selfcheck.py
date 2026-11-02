"""Screenshot retention and playbook self-check (M4-07, §8.4/§13.3).

Screenshots live under logs/gui/, are kept 7 days, never uploaded. The
daily self-check runs each playbook in dry-run (open only, no submit);
repeated failures trip the engine breaker and send a P1 notification with
the latest screenshot attached.
"""

import time
from pathlib import Path

RETENTION_DAYS = 7


def purge_old_screenshots(
    gui_log_dir: Path,
    *,
    now: float | None = None,
    retention_days: int = RETENTION_DAYS,
) -> dict:
    """Delete screenshots older than the retention window."""
    if not gui_log_dir.exists():
        return {"removed": 0, "kept": 0}
    moment = now if now is not None else time.time()
    cutoff = moment - retention_days * 86400
    removed = kept = 0
    for shot in sorted(gui_log_dir.glob("*.png")):
        if shot.stat().st_mtime < cutoff:
            shot.unlink()
            removed += 1
        else:
            kept += 1
    return {"removed": removed, "kept": kept}


def self_check(playbooks: dict, runner=None, *, max_failures: int = 3) -> dict:
    """Dry-run every playbook once; report breaker trips.

    playbooks maps engine_id -> callable dry_run() -> dict. A playbook that
    fails the open-only trial counts as ui_drift; engines hitting
    max_failures consecutive bad days must be tripped by the caller.
    """
    report = {}
    for engine_id, dry_run in playbooks.items():
        result = dry_run()
        status = result.get("status")
        report[engine_id] = {
            "status": status,
            "screenshot": result.get("screenshot"),
            "error_kind": result.get("error_kind"),
        }
    return {"engines": report, "max_failures": max_failures}


def should_trip_breaker(history: list[str], *, fail_threshold: int = 3) -> bool:
    """True when the last fail_threshold self-checks all failed."""
    if len(history) < fail_threshold:
        return False
    return all(entry == "failed" for entry in history[-fail_threshold:])
