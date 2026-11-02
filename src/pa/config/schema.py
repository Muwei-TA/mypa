from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEVEN_TAGS = frozenset(
    {
        "delete_data",
        "stop_service",
        "spend_money",
        "external_message",
        "product_direction",
        "secret_change",
        "permission_change",
    }
)
NEVER_AUTO = frozenset({"delete_data", "spend_money", "secret_change", "permission_change"})


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class Server(Model):
    bind: str = "127.0.0.1:8787"
    public_paths: list[str] = ["/hooks/github", "/hooks/feishu", "/healthz"]

    @field_validator("public_paths")
    @classmethod
    def public_only(cls, value):
        if not set(value) <= {"/hooks/github", "/hooks/feishu", "/healthz"}:
            raise ValueError("public_paths cannot expose private API routes")
        return value


class Worker(Model):
    slots: int = Field(default=3, ge=1)
    reserved_owner_slots: int = Field(default=1, ge=1)
    gui_slots: int = Field(default=1, ge=1, le=1)
    sandbox_slots: int = Field(default=1, ge=1)


class Limits(Model):
    max_retry: int = Field(default=2, ge=0, le=2)
    max_fix_rounds: int = Field(default=3, ge=1, le=3)
    max_review_rounds: int = Field(default=3, ge=1, le=3)
    transient_retries: int = Field(default=3, ge=0, le=3)
    decision_timeout_h: int = Field(default=24, ge=1, le=24)
    default_task_timeout_min: int = Field(default=20, ge=1)
    prepare_timeout_min: int = Field(default=10, ge=1, le=10)


class Dedup(Model):
    default_window_s: int = Field(default=600, ge=1)


class Subscriptions(Model):
    retain_days_after_end: int = Field(default=14, ge=1)


class HumanGate(Model):
    confidence_below: float = Field(default=0.7, ge=0, le=1)
    precedent_min_similarity: float = Field(default=0.85, ge=0.85, le=1)
    never_auto_by_precedent: list[str] = sorted(NEVER_AUTO)

    @field_validator("never_auto_by_precedent")
    @classmethod
    def cannot_remove(cls, value):
        if set(value) != NEVER_AUTO:
            raise ValueError("all four never_auto_by_precedent categories are mandatory")
        return value


class Defaults(Model):
    accept_mode_proactive: Literal["off", "observe", "auto"] = "observe"
    notify: Literal["decision_only", "result", "never"] = "decision_only"


class ConsoleAuth(Model):
    password_hash: str
    totp: str

    @field_validator("password_hash", "totp")
    @classmethod
    def secret_ref(cls, value):
        rest = value.removeprefix("secret://")
        provider, separator, path = rest.partition("/")
        if not value.startswith("secret://") or not provider or not separator or not path:
            raise ValueError("only secret://provider/path references are permitted")
        return value


class Console(Model):
    enabled: bool = False
    auth: ConsoleAuth | None = None


class PAConfig(Model):
    instance: str
    timezone: str = "Asia/Shanghai"
    data_dir: str
    server: Server = Field(default_factory=Server)
    worker: Worker = Field(default_factory=Worker)
    limits: Limits = Field(default_factory=Limits)
    dedup: Dedup = Field(default_factory=Dedup)
    subscriptions: Subscriptions = Field(default_factory=Subscriptions)
    human_gate: HumanGate = Field(default_factory=HumanGate)
    defaults: Defaults = Field(default_factory=Defaults)
    console: Console = Field(default_factory=Console)

    def data_path(self, config_dir: Path) -> Path:
        path = Path(self.data_dir).expanduser()
        return path.resolve() if path.is_absolute() else (config_dir / path).resolve()


class Match(Model):
    source: str | list[str] | None = None
    type: str | list[str] | None = None
    category: str | list[str] | None = None
    skill: str | None = None
    label: str | None = None
    repo_class: str | None = None


class Require(Model):
    actor_in: list[str] = Field(default_factory=list)


class Rule(Model):
    id: str
    rank: int
    enabled: bool = True
    match: Match
    require: Require = Field(default_factory=Require)
    skill: str
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    accept_mode: Literal["off", "observe", "auto"] | None = None
    assess: bool = False
    continue_: bool = Field(default=False, alias="continue")
    merge_by: Literal["subject"] | None = None
    engine_role: str | None = None
    skill_section: str | None = None
    target_dir: str | None = None
    repo: str | None = None
    concurrency_key: str | None = None
    max_parallel: int = Field(default=1, ge=1)
    dedup_window_s: int | None = Field(default=None, ge=1)
    notify: Literal["decision_only", "result", "never"] = "decision_only"


class RulesConfig(Model):
    rules: list[Rule]

    @field_validator("rules")
    @classmethod
    def unique_ids(cls, value):
        if len({r.id for r in value}) != len(value):
            raise ValueError("duplicate rule id")
        return value


class OwnerIdentities(Model):
    github: str | None = None
    gitea: str | None = None
    feishu_open_id: str | None = None


class PrincipalsConfig(Model):
    owner: OwnerIdentities = Field(default_factory=OwnerIdentities)


class EngineConfig(Model):
    adapter: Literal["codex_cli", "hermes_cli", "workbuddy_cli", "gui"]
    command: list[str] = Field(default_factory=list)
    model: str | None = None
    enabled: bool = True
    max_concurrency: int = Field(default=1, ge=1, le=8)
    timeout_s: int = Field(default=180, ge=5, le=1200)
    max_turns: int = Field(default=4, ge=1, le=30)
    max_output_bytes: int = Field(default=2_097_152, ge=1024, le=8_388_608)
    app_path: str | None = None

    @model_validator(mode="after")
    def executable_required(self):
        if self.enabled and self.adapter != "gui" and not self.command:
            raise ValueError("enabled CLI engine requires command")
        return self


class EnginesConfig(Model):
    engines: dict[str, EngineConfig] = Field(default_factory=dict)
    roles: dict[str, list[str]] = Field(default_factory=lambda: {"dev": []})

    @model_validator(mode="after")
    def known_engines(self):
        if any(ref not in self.engines for refs in self.roles.values() for ref in refs):
            raise ValueError("role references an unknown engine")
        return self


class FeishuSource(Model):
    enabled: bool = False
    brand: Literal["feishu", "lark"] = "feishu"
    mode: Literal["long_connection"] = "long_connection"
    app_id: str | None = None
    app_secret: str = "secret://keychain/pa/feishu_app_secret"

    _secret_reference = field_validator("app_secret")(ConsoleAuth.secret_ref.__func__)

    @model_validator(mode="after")
    def app_required(self):
        if self.enabled and not self.app_id:
            raise ValueError("enabled Feishu source requires app_id")
        return self


class Sources(Model):
    feishu: FeishuSource = Field(default_factory=FeishuSource)


class SourcesConfig(Model):
    sources: Sources = Field(default_factory=Sources)


class NotifyTarget(Model):
    type: Literal["user", "chat"] = "user"
    id: str | None = None


class NotifyConfig(Model):
    target: NotifyTarget = Field(default_factory=NotifyTarget)


CONFIG_MODELS: dict[str, type[Model]] = {
    "pa.yaml": PAConfig,
    "rules.yaml": RulesConfig,
    "principals.yaml": PrincipalsConfig,
    "engines.yaml": EnginesConfig,
    "sources.yaml": SourcesConfig,
    "notify.yaml": NotifyConfig,
}
OPTIONAL_CONFIGS = {"engines.yaml", "sources.yaml", "notify.yaml"}


class LoadedConfig(Model):
    main: PAConfig
    rules: RulesConfig
    principals: PrincipalsConfig
    engines: EnginesConfig = Field(default_factory=EnginesConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    policy_version: str
    raw: dict[str, Any]
