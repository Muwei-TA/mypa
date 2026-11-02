from enum import StrEnum

from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from pa.config.schema import Model


class TaskStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    AWAITING_CI = "awaiting_ci"
    AWAITING_DECISION = "awaiting_decision"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    JUDGED = "judged"
    CLOSED = "closed"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class Reason(Model):
    code: str
    values: dict = {}


def metadata(engine: Engine) -> MetaData:
    """SQLAlchemy Core models reflect the versioned, PRD-defined schema."""
    result = MetaData()
    result.reflect(bind=engine)
    return result
