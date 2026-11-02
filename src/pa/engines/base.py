from pathlib import Path

from pydantic import Field

from pa.config.schema import Model
from pa.skills.receipt import Receipt, Usage


class EngineRequest(Model):
    task_id: str
    prompt: str
    output_dir: Path


class EngineResult(Model):
    engine_id: str
    receipt: Receipt | None = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
    error_kind: str | None = None
    log_path: str | None = None
