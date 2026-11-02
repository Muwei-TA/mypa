"""Source read cursors (M5-06, PRD §5.1/v1.1·D19).

Polling sources persist their read position so restarts never replay
events. Single-read failures don't move the cursor; three consecutive
failures escalate to a report.
"""

from sqlalchemy import text


def read_cursor(connection, source_id: str) -> str | None:
    row = connection.execute(
        text("SELECT cursor FROM source_cursors WHERE source_id=:s"), {"s": source_id}
    ).first()
    return row[0] if row else None


def advance_cursor(connection, source_id: str, cursor: str) -> None:
    """Persist a successful read position and clear the failure streak."""
    from pa.common import now

    connection.execute(
        text("""
        INSERT INTO source_cursors(source_id,cursor,updated_at,consecutive_failures,last_error)
        VALUES(:s,:c,:at,0,NULL)
          ON CONFLICT(source_id) DO UPDATE SET cursor=:c,updated_at=:at,
            consecutive_failures=0,last_error=NULL
    """),
        {"s": source_id, "c": cursor, "at": now()},
    )


def record_failure(connection, source_id: str, error: str, *, threshold: int = 3) -> dict:
    """Count one failed poll; report only after threshold consecutive failures."""
    from pa.common import now

    connection.execute(
        text("""
        INSERT INTO source_cursors(source_id,cursor,updated_at,consecutive_failures,last_error)
        VALUES(:s,NULL,:at,1,:err)
          ON CONFLICT(source_id) DO UPDATE SET consecutive_failures=consecutive_failures+1,
            last_error=:err,updated_at=:at
    """),
        {"s": source_id, "at": now(), "err": error},
    )
    streak = connection.execute(
        text("SELECT consecutive_failures FROM source_cursors WHERE source_id=:s"),
        {"s": source_id},
    ).scalar_one()
    return {"source_id": source_id, "streak": streak, "report": streak >= threshold}
