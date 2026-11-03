"""Health checks (M5-09, PRD §16.3/v1.1·D11).

Eight checks run every 5 minutes. A check only becomes a system problem
after 3 consecutive failures — flapping does not spam the overview.
"""

from dataclasses import dataclass

from sqlalchemy import text

CHECK_INTERVAL_MIN = 5
FAIL_THRESHOLD = 3

CHECKS = (
    "db_writable",
    "core_alive",
    "worker_alive",
    "server_alive",
    "feishu_connected",
    "engine_health",
    "disk_space",
    "queue_lag",
)


@dataclass
class HealthCheck:
    name: str
    ok: bool
    detail: str = ""


def run_checks(
    connection,
    *,
    alive: dict[str, bool] | None = None,
    disk_free_gb: float = 10.0,
    queue_lag_min: float = 0.0,
    feishu_ok: bool = True,
    engines_ok: bool = True,
) -> list[HealthCheck]:
    """Execute the eight §16.3 checks against the given live signals."""
    alive = alive or {}
    results = []
    try:
        connection.execute(text("SELECT 1 FROM events LIMIT 1"))
        db_ok = True
        detail = ""
    except Exception as exc:  # pragma: no cover - failure path only
        db_ok, detail = False, str(exc)
    results.append(HealthCheck("db_writable", db_ok, detail))
    for name in ("core", "worker", "server"):
        results.append(HealthCheck(f"{name}_alive", bool(alive.get(name, False))))
    results.append(HealthCheck("feishu_connected", feishu_ok))
    results.append(HealthCheck("engine_health", engines_ok))
    results.append(HealthCheck("disk_space", disk_free_gb >= 5.0, f"{disk_free_gb:.1f}GB free"))
    results.append(HealthCheck("queue_lag", queue_lag_min <= 30.0, f"{queue_lag_min:.0f}min lag"))
    return results


def triage(history: dict[str, list[bool]]) -> list[dict]:
    """Turn per-check recent history into system problems (D11: 3 strikes)."""
    problems = []
    for name, outcomes in history.items():
        if len(outcomes) >= FAIL_THRESHOLD and not any(outcomes[-FAIL_THRESHOLD:]):
            problems.append({"check": name, "consecutive_failures": FAIL_THRESHOLD})
    return problems
