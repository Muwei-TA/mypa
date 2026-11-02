"""Skill loading and prompt rendering (PRD §7).

Skills are packaged data under ``pa/skills/assets/<skill_id>/``. This module
validates ``skill.yaml`` and renders the Jinja2 ``prompt.md`` template.
"""

from importlib.resources import files
from typing import Literal

import yaml
from jinja2 import BaseLoader, Environment
from pydantic import Field, model_validator

from pa.config.schema import SEVEN_TAGS, Model

ASSETS = files("pa.skills").joinpath("assets")


class SkillEngineRef(Model):
    role: str | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def exactly_one_selector(self):
        if (self.role is None) == (self.ref is None):
            raise ValueError("engine entry needs exactly one of role/ref")
        return self


class SkillPermissions(Model):
    network: Literal["none", "allowlist", "any"]
    network_allow: list[str] = Field(default_factory=list)
    fs: str
    repo_write: bool


class SkillHumanGate(Model):
    confidence_below: float = Field(ge=0, le=1)
    always_on: list[str]
    use_precedents: bool

    @model_validator(mode="after")
    def seven_tags_mandatory(self):
        if set(self.always_on) != SEVEN_TAGS:
            raise ValueError("always_on must contain exactly the seven mandatory tags")
        return self


class SkillDelivery(Model):
    ui_change_requires_screenshots: bool
    draft_must_be_marked: bool


class SkillCommunication(Model):
    ack: str
    max_chat_words: int = Field(ge=1)


class SkillBudget(Model):
    per_task_tokens: int = Field(ge=1)
    per_task_turns: int = Field(ge=1)
    daily_cny: int | None = None


class SkillMemory(Model):
    read: list[str]
    write: str


class SkillSpec(Model):
    id: str
    name: str
    version: int = Field(ge=1)
    enabled: bool
    rank: int
    priority: Literal["P0", "P1", "P2", "P3"]
    concurrency: int = Field(ge=1)
    timeout_min: int = Field(ge=1)
    mode: Literal["auto", "direct", "code"]
    engines: list[SkillEngineRef]
    allowed_tiers: list[str]
    blocked_commands: list[str]
    tools: list[str]
    permissions: SkillPermissions
    risk_ceiling: Literal["low", "medium", "high"]
    human_gate: SkillHumanGate
    delivery: SkillDelivery
    communication: SkillCommunication
    budget: SkillBudget
    steps: list[str]
    sections: dict[str, str]
    output_schema: str
    memory: SkillMemory


def load_skill(skill_id: str = "sample-direct") -> SkillSpec:
    raw = yaml.safe_load(ASSETS.joinpath(skill_id, "skill.yaml").read_text())
    return SkillSpec.model_validate(raw)


def list_skills() -> list[str]:
    return sorted(path.name for path in ASSETS.iterdir() if path.is_dir())


def render_prompt(skill_id: str, *, inputs: str) -> str:
    source = ASSETS.joinpath(skill_id, "prompt.md").read_text()
    environment = Environment(loader=BaseLoader(), trim_blocks=True, lstrip_blocks=True)
    return environment.from_string(source).render(inputs=inputs)
