"""Secret rotation reminders (M5-11, PRD §17.4).

secrets_meta tracks rotation metadata only, never values. Reminders: P2 at
7 days before expiry, P1 on the day, then every 3 days after. A rotation
completes only after a connectivity test passes.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

REMIND_BEFORE_DAYS = 7
REMIND_AFTER_DAYS_INTERVAL = 3


def register(connection, *, ref: str, provider: str, rotate_every_days: int) -> None:
    """Track one secret's rotation metadata."""
    from pa.common import now

    connection.execute(
        text("""
        INSERT INTO secrets_meta(ref,provider,created_at,rotated_at,rotate_every_days)
        VALUES(:ref,:provider,:at,:at,:every)
          ON CONFLICT(ref) DO UPDATE SET provider=:provider,rotate_every_days=:every
    """),
        {"ref": ref, "provider": provider, "at": now(), "every": rotate_every_days},
    )


def due(connection, *, at: datetime | None = None) -> list[dict]:
    """List secrets with their reminder level (P2/P1/none)."""
    moment = at or datetime.now(UTC)
    rows = connection.execute(
        text("SELECT ref,provider,rotated_at,rotate_every_days,last_reminded_at FROM secrets_meta")
    ).mappings()
    result = []
    for row in rows:
        rotated = datetime.fromisoformat(row["rotated_at"]) if row["rotated_at"] else None
        if rotated is None:
            continue
        expiry = rotated + timedelta(days=row["rotate_every_days"])
        days_left = (expiry - moment).days
        level = None
        if days_left <= 0:
            level = "P1"
        elif days_left <= REMIND_BEFORE_DAYS:
            level = "P2"
        if level:
            last = (
                datetime.fromisoformat(row["last_reminded_at"])
                if row["last_reminded_at"]
                else None
            )
            # P1 re-reminds every 3 days after expiry; P2 once inside the window
            if level == "P1" and last and (moment - last).days < REMIND_AFTER_DAYS_INTERVAL:
                level = None
            if level == "P2" and last and (moment - last).days < REMIND_BEFORE_DAYS:
                level = None
            if level:
                result.append(
                    {"ref": row["ref"], "provider": row["provider"], "level": level,
                     "days_left": days_left}
                )
    return result


def complete_rotation(connection, ref: str, *, connectivity_ok: bool) -> dict:
    """Mark a rotation done only when the connectivity test passes."""
    if not connectivity_ok:
        return {"ref": ref, "rotated": False, "reason": "connectivity test failed"}
    from pa.common import now

    connection.execute(
        text("UPDATE secrets_meta SET rotated_at=:at WHERE ref=:ref"),
        {"at": now(), "ref": ref},
    )
    return {"ref": ref, "rotated": True}
