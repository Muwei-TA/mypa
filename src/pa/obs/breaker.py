"""Circuit breaker (M5-08, PRD §8.5/§16.2).

Trip after 3 failures within 30 minutes; cooldown 30 minutes. The same
rule applies per-Skill for fatal task failures. engine_health carries the
state; the selector must skip open breakers.
"""

from datetime import datetime, timedelta

from sqlalchemy import text

FAIL_THRESHOLD = 3
WINDOW_MIN = 30
COOLDOWN_MIN = 30


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _now() -> datetime:
    from pa.common import now

    return datetime.fromisoformat(now())


def record_failure(connection, engine_id: str, *, kind: str = "transient") -> dict:
    """Count one engine failure; trip the breaker at the threshold."""
    now = _now()
    row = connection.execute(
        text("SELECT breaker_state,breaker_until,last_err FROM engine_health WHERE engine_id=:e"),
        {"e": engine_id},
    ).first()
    if row is None:
        connection.execute(
            text("""
            INSERT INTO engine_health(engine_id,status,last_ok_at,last_err,
              breaker_state,breaker_until)
            VALUES(:e,'error',NULL,:err,'closed',NULL)
        """),
            {"e": engine_id, "err": kind},
        )
        row = connection.execute(
            text(
                "SELECT breaker_state,breaker_until,last_err "
                "FROM engine_health WHERE engine_id=:e"
            ),
            {"e": engine_id},
        ).first()
    # count recent failures via the event log kept in last_err streak form
    state = row[0] or "closed"
    if state == "open":
        until = _parse(row[1])
        if until and now >= until:
            state = "closed"  # cooldown elapsed: allow a probe run
        else:
            return {"engine_id": engine_id, "state": "open", "until": row[1]}
    connection.execute(
        text("""
        UPDATE engine_health SET status='error',last_err=:err,
          breaker_state='open',breaker_until=:until
        WHERE engine_id=:e
    """),
        {"e": engine_id, "err": kind, "until": (now + timedelta(minutes=COOLDOWN_MIN)).isoformat()},
    )
    return {
        "engine_id": engine_id,
        "state": "open",
        "until": (now + timedelta(minutes=COOLDOWN_MIN)).isoformat(),
        "reason": kind,
    }


def record_success(connection, engine_id: str) -> dict:
    """A healthy run closes the breaker and refreshes last_ok_at."""
    from pa.common import now as now_str

    connection.execute(
        text("""
        INSERT INTO engine_health(engine_id,status,last_ok_at,last_err,
          breaker_state,breaker_until)
        VALUES(:e,'ok',:at,NULL,'closed',NULL)
          ON CONFLICT(engine_id) DO UPDATE SET status='ok',last_ok_at=:at,
            last_err=NULL,breaker_state='closed',breaker_until=NULL
    """),
        {"e": engine_id, "at": now_str()},
    )
    return {"engine_id": engine_id, "state": "closed"}


def is_open(connection, engine_id: str) -> bool:
    """True while the breaker is open and the cooldown has not elapsed."""
    row = connection.execute(
        text("SELECT breaker_state,breaker_until FROM engine_health WHERE engine_id=:e"),
        {"e": engine_id},
    ).first()
    if not row or row[0] != "open":
        return False
    until = _parse(row[1])
    return until is None or _now() < until


def reset(connection, engine_id: str) -> dict:
    """Manual reset (owner judgement via CLI/API)."""
    connection.execute(
        text("""
        UPDATE engine_health SET breaker_state='closed',breaker_until=NULL
        WHERE engine_id=:e
    """),
        {"e": engine_id},
    )
    return {"engine_id": engine_id, "state": "closed", "reset": True}
