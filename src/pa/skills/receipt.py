import json
from importlib.resources import files
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import Field, field_validator

from pa.config.schema import SEVEN_TAGS, Model


class Usage(Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    duration_s: float = Field(default=0.0, ge=0)


class Artifact(Model):
    type: Literal["pr", "file", "text", "url"]
    ref: str


class HumanAction(Model):
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=1)
    recommend: str = ""
    why: str = Field(default="", max_length=60)

    @field_validator("question")
    @classmethod
    def single_question(cls, value):
        if value.count("?") + value.count("？") > 1:
            raise ValueError("only one question is permitted")
        return value


class Handback(Model):
    event_id: str
    reason: str


class Receipt(Model):
    status: Literal["succeeded", "failed", "needs_human", "blocked", "dropped"]
    summary: str = Field(min_length=1)
    code_changed: bool
    confidence: float = Field(ge=0, le=1)
    risk: Literal["low", "medium", "high"]
    evidence: list[str]
    usage: Usage
    artifacts: list[Artifact] = Field(default_factory=list)
    draft: bool = False
    risk_reasons: list[str] = Field(default_factory=list)
    tags: list[
        Literal[
            "delete_data",
            "stop_service",
            "spend_money",
            "external_message",
            "product_direction",
            "secret_change",
            "permission_change",
        ]
    ] = Field(default_factory=list)
    suggested_action: Literal["close", "merge", "ask_owner", "retry"] | None = None
    human_action: HumanAction | None = None
    handback: list[Handback] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def mandatory_categories(cls, value):
        if not set(value) <= SEVEN_TAGS or len(set(value)) != len(value):
            raise ValueError("invalid or duplicate human gate tag")
        return value


def parse_receipt(value: str | dict) -> Receipt:
    if isinstance(value, str):
        value = json.loads(value)
    # Strict-output providers require explicit nulls; PRD schema uses absent optional fields.
    value = {k: v for k, v in value.items() if v is not None}
    schema = json.loads(files("pa.skills").joinpath("receipt.schema.json").read_text())
    Draft202012Validator(schema).validate(value)
    return Receipt.model_validate(value)


def output_schema() -> dict:
    schema = Receipt.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            node.pop("default", None)
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for item in node:
                strict(item)

    strict(schema)
    return schema
