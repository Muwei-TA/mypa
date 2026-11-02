from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, field_validator

from pa.common import new_id
from pa.config.schema import Model


class Event(Model):
    id: str = Field(default_factory=new_id)
    source: str
    type: str
    category: Literal["schedule", "alert", "error", "feedback", "new_source", "decision", "manual"]
    actor: str | None = None
    subject: str | None = None
    payload: dict = Field(default_factory=dict)
    dedup_key: str = Field(min_length=1)
    received_at: datetime
    trust: Literal["verified", "unverified"]
    summary: str = Field(max_length=80)
    link: str | None = None
    threads: list[str] = Field(default_factory=list)
    addressed: bool = False

    @field_validator("received_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must have a timezone")
        return value


class IngressAdapter(Protocol):
    name: str

    def parse(self, request) -> Event: ...


class WebhookAdapter(IngressAdapter, Protocol):
    def verify(self, request) -> bool: ...


class PollingAdapter(IngressAdapter, Protocol):
    async def poll(self, cursor: str | None) -> tuple[list[Event], str]: ...
