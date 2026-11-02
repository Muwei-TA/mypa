from pathlib import Path

from pydantic import Field

from pa.config.schema import Model
from pa.skills.receipt import Receipt, Usage


class EngineRequest(Model):
    task_id: str
    prompt: str
    output_dir: Path
    max_turns: int | None = Field(default=None, ge=1)


class EngineResult(Model):
    engine_id: str
    receipt: Receipt | None = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
    error_kind: str | None = None
    log_path: str | None = None
    # Adapter-reported model/API iterations, never taken from the model receipt.
    turns: int | None = Field(default=None, ge=0)
