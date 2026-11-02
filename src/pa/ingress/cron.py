from datetime import UTC, datetime

from pa.ingress.base import Event


class CronAdapter:
    """Normalize a tick using its planned time, including delivery after a restart."""

    name = "cron"

    def parse(self, request: dict) -> Event:
        scheduled_at = datetime.fromisoformat(request["scheduled_at"])
        if scheduled_at.tzinfo is None:
            raise ValueError("scheduled_at must have a timezone")
        slot = scheduled_at.astimezone(UTC).isoformat()
        return Event(
            source=self.name,
            type=request["event_type"],
            category="schedule",
            actor="owner",
            trust="verified",
            addressed=False,
            received_at=datetime.now(UTC),
            dedup_key=f"{request['id']}:{slot}",
            summary=request.get("summary", request["id"])[:80],
            payload=request.get("payload", {}),
        )
