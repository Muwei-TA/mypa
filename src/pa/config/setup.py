import shutil
from pathlib import Path

import yaml

from pa.common import new_id
from pa.config.loader import load


def save_files(config_dir: Path, values: dict):
    """Validate a complete staged snapshot, then replace files with a recoverable backup."""
    stage = config_dir / (".stage-" + new_id())
    stage.mkdir()
    try:
        for source in config_dir.glob("*.yaml"):
            shutil.copy2(source, stage / source.name)
        for name, value in values.items():
            (stage / name).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        load(stage)
        backup = config_dir / ".backups" / new_id()
        backup.mkdir(parents=True)
        previous = {}
        for name in values:
            destination = config_dir / name
            previous[name] = destination.exists()
            if destination.exists():
                shutil.copy2(destination, backup / name)
        try:
            for name in values:
                (stage / name).replace(config_dir / name)
        except BaseException:
            for name, existed in previous.items():
                if existed:
                    shutil.copy2(backup / name, config_dir / name)
                else:
                    (config_dir / name).unlink(missing_ok=True)
            raise
        return backup
    finally:
        shutil.rmtree(stage)


def configure_feishu(config_dir, *, app_id, owner_open_id=None, chat_id=None):
    config = load(config_dir)
    sources = config.sources.model_dump(mode="json")
    sources["sources"]["feishu"].update({"app_id": app_id, "enabled": True})
    principals = config.principals.model_dump(mode="json")
    if owner_open_id:
        principals["owner"]["feishu_open_id"] = owner_open_id
    target_id = chat_id or owner_open_id or config.notify.target.id
    rules = config.rules.model_dump(mode="json", by_alias=True)
    if not any(rule["id"] == "r-feishu-direct" for rule in rules["rules"]):
        rules["rules"].append(
            {
                "id": "r-feishu-direct",
                "rank": 1500,
                "match": {"source": "feishu", "type": "feishu.message"},
                "require": {"actor_in": ["owner"]},
                "skill": "sample-direct",
                "notify": "result",
            }
        )
    return save_files(
        config_dir,
        {
            "sources.yaml": sources,
            "principals.yaml": principals,
            "rules.yaml": rules,
            "notify.yaml": {"target": {"type": "chat" if chat_id else "user", "id": target_id}},
        },
    )
