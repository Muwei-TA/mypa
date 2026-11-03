import hashlib
import json
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from pa.common import PAError
from pa.config.schema import CONFIG_MODELS, OPTIONAL_CONFIGS, LoadedConfig


class UniqueLoader(yaml.SafeLoader):
    pass


# YAML 1.2 booleans: accept_mode: off must remain a string.
UniqueLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise PAError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def expand(value):
    if isinstance(value, str):

        def replace(match):
            name = match.group(1)
            if name not in os.environ:
                raise PAError(f"missing environment variable: {name}")
            return os.environ[name]

        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


def load(config_dir: Path) -> LoadedConfig:
    raw, models = {}, {}
    supported = set(CONFIG_MODELS)
    unknown = {p.name for p in config_dir.glob("*.yaml")} - supported
    if unknown:
        raise PAError(f"configuration files not implemented yet: {', '.join(sorted(unknown))}")
    for name, model in CONFIG_MODELS.items():
        path = config_dir / name
        try:
            value = (
                {}
                if name in OPTIONAL_CONFIGS and not path.exists()
                else expand(yaml.load(path.read_text(), Loader=UniqueLoader))
            )
            models[name] = model.model_validate(value)
            raw[name] = models[name].model_dump(mode="json", by_alias=True)
        except FileNotFoundError:
            raise PAError(f"missing configuration file: {path}", "not_found", 4) from None
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['type']}"
                for e in exc.errors(include_input=False)
            )
            raise PAError(f"{name}: {details}") from None
        except yaml.YAMLError:
            raise PAError(f"{name}: invalid YAML") from None
    canon = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return LoadedConfig(
        main=models["pa.yaml"],
        rules=models["rules.yaml"],
        principals=models["principals.yaml"],
        engines=models["engines.yaml"],
        sources=models["sources.yaml"],
        notify=models["notify.yaml"],
        schedules=models["schedules.yaml"],
        merge_policy=models["merge_policy.yaml"],
        code=models["code.yaml"],
        raw=raw,
        policy_version="p-" + hashlib.sha256(canon.encode()).hexdigest()[:6],
    )


def initialize(config_dir: Path) -> None:
    if config_dir.exists() and any(config_dir.iterdir()):
        raise PAError(f"configuration directory is not empty: {config_dir}", "conflict")
    config_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "pa.yaml": {"instance": "pa-local", "data_dir": "../data"},
        "rules.yaml": {
            "rules": [
                {
                    "id": "r-manual-direct",
                    "rank": 1000,
                    "match": {"source": "manual", "skill": "sample-direct"},
                    "skill": "sample-direct",
                }
            ]
        },
        "principals.yaml": {"owner": {}},
        "engines.yaml": {},
        "sources.yaml": {},
        "notify.yaml": {},
        "schedules.yaml": {"schedules": []},
    }
    for name, value in defaults.items():
        (config_dir / name).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
