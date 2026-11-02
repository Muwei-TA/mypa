from datetime import UTC, datetime

from pa.common import new_id
from pa.ingress.base import Event


class ManualAdapter:
    name = "manual"

    def parse(self, request: dict) -> Event:
        return Event(
            source=self.name,
            type=request["type"],
            category="manual",
            actor="owner",
            trust="verified",
            addressed=True,
            received_at=datetime.now(UTC),
            dedup_key=request.get("dedup_key") or new_id(),
            summary=request.get("summary", request["type"]),
            payload=request.get("payload", {}),
            subject=request.get("subject"),
        )
