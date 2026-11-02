import json
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pa import __version__
from pa.common import PAError
from pa.config.loader import initialize, load
from pa.obs.logging import configure
from pa.obs.redact import redact
from pa.plugins import GROUPS, discover
from pa.store.db import migrate
from pa.store.facades import OwnerFacade

app = typer.Typer(no_args_is_help=True, help="PA 私人 AI 自动化系统（M1 基础开发版）")
config_app = typer.Typer(no_args_is_help=True, help="配置校验与只读查询")
event_app = typer.Typer(no_args_is_help=True, help="手动事件接入")
task_app = typer.Typer(no_args_is_help=True, help="任务查询与取消")
observe_app = typer.Typer(no_args_is_help=True, help="观察记录")
effect_app = typer.Typer(no_args_is_help=True, help="发件箱（本阶段仅入库）")
plugin_app = typer.Typer(no_args_is_help=True, help="查看插件扩展点")
for name, group in (
    ("config", config_app),
    ("event", event_app),
    ("task", task_app),
    ("observe", observe_app),
    ("effect", effect_app),
    ("plugin", plugin_app),
):
    app.add_typer(group, name=name)

JsonOption = Annotated[bool, typer.Option("--json", help="输出 JSON")]


def guarded(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PAError as exc:
            output({"error": {"code": exc.code, "message": str(exc)}}, error=True)
            raise typer.Exit(exc.exit_code) from None
        except ValidationError as exc:
            output(
                {
                    "error": {
                        "code": "validation_error",
                        "details": exc.errors(include_input=False, include_context=False),
                    }
                },
                error=True,
            )
            raise typer.Exit(2) from None
        except (OSError, SQLAlchemyError):
            # Avoid SQLAlchemy's SQL/parameter dump and filesystem contents in error output.
            output(
                {
                    "error": {
                        "code": "storage_error",
                        "message": "Storage operation failed. Run pa migrate / pa doctor.",
                    }
                },
                error=True,
            )
            raise typer.Exit(2) from None

    return wrapped


def output(value, error=False):
    typer.echo(json.dumps(redact(value), ensure_ascii=False, indent=2, default=str), err=error)


@app.callback()
def main(
    ctx: typer.Context,
    config_dir: Annotated[Path, typer.Option("--config-dir", envvar="PA_CONFIG_DIR")] = Path(
        "~/.config/pa"
    ),
    json_output: JsonOption = False,
):
    configure()
    ctx.obj = {"config_dir": config_dir.expanduser().resolve()}


def config_path(ctx):
    return ctx.obj["config_dir"]


@contextmanager
def owner(ctx):
    facade = OwnerFacade(config_path(ctx))
    try:
        yield facade
    finally:
        facade.close()


@app.command()
@guarded
def init(ctx: typer.Context, json_output: JsonOption = False):
    """创建基础配置和本地数据库；已有配置不会被覆盖。"""
    initialize(config_path(ctx))
    with owner(ctx) as facade:
        migrate(facade.engine)
        output({"config_dir": str(config_path(ctx)), "initialized": True})


@app.command("migrate")
@guarded
def migrate_command(ctx: typer.Context, json_output: JsonOption = False):
    """将数据库升级到当前版本。"""
    with owner(ctx) as facade:
        migrate(facade.engine)
        output({"revision": "0001_initial"})


@app.command()
@guarded
def doctor(ctx: typer.Context, json_output: JsonOption = False):
    """检查本阶段已实现的配置、数据库和迁移版本。"""
    with owner(ctx) as facade:
        with facade.engine.connect() as connection:
            integrity = connection.exec_driver_sql("PRAGMA quick_check").scalar_one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            wal = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            checks = [
                {"name": "config", "ok": True},
                {"name": "db", "ok": integrity == "ok"},
                {"name": "migration", "ok": revision == "0001_initial"},
                {"name": "wal", "ok": wal == "wal"},
            ]
            output({"checks": checks, "version": __version__})
            if not all(c["ok"] for c in checks):
                raise typer.Exit(2)


@config_app.command("validate")
@guarded
def config_validate(ctx: typer.Context, json_output: JsonOption = False):
    loaded = load(config_path(ctx))
    output({"ok": True, "files": sorted(loaded.raw), "policy_version": loaded.policy_version})


@config_app.command("show")
@guarded
def config_show(ctx: typer.Context, file: str = "pa.yaml", json_output: JsonOption = False):
    loaded = load(config_path(ctx))
    if file not in loaded.raw:
        raise PAError("unsupported configuration file", "not_found", 4)
    output(loaded.raw[file])


@config_app.command("get")
@guarded
def config_get(ctx: typer.Context, key: str, json_output: JsonOption = False):
    parts = key.split(".")
    loaded = load(config_path(ctx))
    value = loaded.raw.get(parts[0] + ".yaml")
    for part in parts[1:]:
        if not isinstance(value, dict) or part not in value:
            raise PAError("configuration key not found", "not_found", 4)
        value = value[part]
    if value is None:
        raise PAError("configuration key not found", "not_found", 4)
    output({"key": key, "value": value})


@event_app.command("emit")
@guarded
def event_emit(
    ctx: typer.Context,
    type_: Annotated[str, typer.Argument(help="事件类型，如 manual.test")],
    payload: Annotated[str, typer.Option()] = "{}",
    summary: Annotated[str | None, typer.Option()] = None,
    dedup_key: Annotated[str | None, typer.Option()] = None,
    json_output: JsonOption = False,
):
    try:
        data = json.loads(payload)
    except ValueError:
        raise PAError("payload must be valid JSON") from None
    if not isinstance(data, dict):
        raise PAError("payload must be a JSON object")
    with owner(ctx) as facade:
        adapter = discover("pa.ingress")["manual"].load()()
        event = adapter.parse(
            {
                "type": type_,
                "payload": data,
                "summary": summary or type_,
                "dedup_key": dedup_key,
            }
        )
        output(facade.emit(event))


@event_app.command("list")
@guarded
def event_list(ctx: typer.Context, limit: int = 50, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.list("events", limit))


@event_app.command("show")
@guarded
def event_show(ctx: typer.Context, id: str, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.show("events", id))


@task_app.command("list")
@guarded
def task_list(ctx: typer.Context, limit: int = 50, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.list("tasks", limit))


@task_app.command("show")
@guarded
def task_show(ctx: typer.Context, id: str, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.show("tasks", id))


@task_app.command("cancel")
@guarded
def task_cancel(ctx: typer.Context, id: str, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.cancel(id))


@observe_app.command("list")
@guarded
def observe_list(ctx: typer.Context, limit: int = 50, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.list("observations", limit))


@effect_app.command("list")
@guarded
def effect_list(ctx: typer.Context, limit: int = 50, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.list("effects", limit))


@plugin_app.command("list")
@guarded
def plugin_list(json_output: JsonOption = False):
    output({g: sorted(discover(g)) for g in GROUPS})


@app.command()
@guarded
def pause(ctx: typer.Context, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.pause())


@app.command()
@guarded
def resume(ctx: typer.Context, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.resume())


@app.command()
@guarded
def status(ctx: typer.Context, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.status())
