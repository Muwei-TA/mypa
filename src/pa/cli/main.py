import asyncio
import json
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pa import __version__
from pa.common import PAError, new_id
from pa.config.loader import initialize, load
from pa.notify.feishu import FeishuError
from pa.obs.logging import configure
from pa.obs.redact import redact
from pa.plugins import GROUPS, discover
from pa.store.db import SCHEMA_HEAD, migrate
from pa.store.facades import OwnerFacade

app = typer.Typer(no_args_is_help=True, help="PA 私人 AI 自动化系统（M1 基础开发版）")
config_app = typer.Typer(no_args_is_help=True, help="配置校验与只读查询")
event_app = typer.Typer(no_args_is_help=True, help="手动事件接入")
task_app = typer.Typer(no_args_is_help=True, help="任务查询与取消")
observe_app = typer.Typer(no_args_is_help=True, help="观察记录")
effect_app = typer.Typer(no_args_is_help=True, help="发件箱（本阶段仅入库）")
plugin_app = typer.Typer(no_args_is_help=True, help="查看插件扩展点")
engine_app = typer.Typer(no_args_is_help=True, help="本机 Agent 发现与连接测试")
secret_app = typer.Typer(no_args_is_help=True, help="密钥安全输入与只读检查")
feishu_app = typer.Typer(no_args_is_help=True, help="飞书长连接配置、验证与本人配对")
service_app = typer.Typer(no_args_is_help=True, help="macOS 后台服务")
skill_app = typer.Typer(no_args_is_help=True, help="Skill 全局排序")
rule_app = typer.Typer(no_args_is_help=True, help="规则查询与接单模式")
archive_app = typer.Typer(no_args_is_help=True, help="快照归档")
backup_app = typer.Typer(no_args_is_help=True, help="备份快照与恢复")
cost_app = typer.Typer(no_args_is_help=True, help="成本汇总")
budget_app = typer.Typer(no_args_is_help=True, help="预算管理")
memory_app = typer.Typer(no_args_is_help=True, help="记忆库")
sandbox_app = typer.Typer(no_args_is_help=True, help="一次性沙盒运行")
for name, group in (
    ("config", config_app),
    ("event", event_app),
    ("task", task_app),
    ("observe", observe_app),
    ("effect", effect_app),
    ("plugin", plugin_app),
    ("engine", engine_app),
    ("secret", secret_app),
    ("feishu", feishu_app),
    ("service", service_app),
    ("skill", skill_app),
    ("rule", rule_app),
    ("archive", archive_app),
    ("backup", backup_app),
    ("cost", cost_app),
    ("budget", budget_app),
    ("memory", memory_app),
    ("sandbox", sandbox_app),
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
        except FeishuError as exc:
            output({"error": {"code": "feishu_error", "message": str(exc)}}, error=True)
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


def _load_json_argument(raw: str) -> dict:
    """Accept @file.json, a bare file path, or inline JSON text."""
    import json as _json

    text_value = raw
    if raw.startswith("@"):
        text_value = Path(raw[1:]).expanduser().read_text()
    elif not raw.strip().startswith(("{", "[")):
        candidate = Path(raw).expanduser()
        if candidate.exists():
            text_value = candidate.read_text()
    value = _json.loads(text_value)
    if not isinstance(value, dict):
        raise typer.BadParameter("JSON 对象 expected")
    return value


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
        output({"revision": SCHEMA_HEAD})


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
                {"name": "migration", "ok": revision == SCHEMA_HEAD},
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


@task_app.command("move")
@guarded
def task_move(
    ctx: typer.Context,
    id: str,
    before: Annotated[str | None, typer.Option(help="移动到该任务之前")] = None,
    to_top: Annotated[bool, typer.Option("--to-top")] = False,
    to_bottom: Annotated[bool, typer.Option("--to-bottom")] = False,
    json_output: JsonOption = False,
):
    with owner(ctx) as facade:
        output(facade.move_task(id, before=before, to_top=to_top, to_bottom=to_bottom))


@task_app.command("pin")
@guarded
def task_pin(ctx: typer.Context, id: str, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.set_pinned(id, True))


@task_app.command("unpin")
@guarded
def task_unpin(ctx: typer.Context, id: str, json_output: JsonOption = False):
    with owner(ctx) as facade:
        output(facade.set_pinned(id, False))


@task_app.command("priority")
@guarded
def task_priority(
    ctx: typer.Context,
    id: str,
    priority: Annotated[str, typer.Argument(help="P0/P1/P2/P3")],
    json_output: JsonOption = False,
):
    with owner(ctx) as facade:
        output(facade.set_priority(id, priority))


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


@effect_app.command("retry")
@guarded
def effect_retry(ctx: typer.Context, id: str, json_output: JsonOption = False):
    """重新排队一个发件箱条目（owner 判断后人工重试）。"""
    with owner(ctx) as facade:
        output(facade.effect_retry(id))


@effect_app.command("drop")
@guarded
def effect_drop(
    ctx: typer.Context,
    id: str,
    reason: Annotated[str, typer.Option(help="丢弃原因")] = "owner_dropped",
    json_output: JsonOption = False,
):
    """丢弃一个发件箱条目；已完成的条目不可丢弃。"""
    with owner(ctx) as facade:
        output(facade.effect_drop(id, reason=reason))


@rule_app.command("mode")
@guarded
def rule_mode(
    ctx: typer.Context,
    id: str,
    mode: Annotated[str, typer.Argument(help="off / observe / auto")],
    json_output: JsonOption = False,
):
    """修改规则接单模式，写回 rules.yaml 并记审计。"""
    with owner(ctx) as facade:
        output(facade.set_rule_mode(id, mode))


@rule_app.command("test")
@guarded
def rule_test(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument(help="示例事件 JSON 文件路径（@file），或内联 JSON")],
    json_output: JsonOption = False,
):
    """用示例事件测试规则命中情况，不实际执行。"""
    from pa.console.testing import dry_run_rules

    payload = _load_json_argument(event)
    output(dry_run_rules(config_path(ctx), payload))


@rule_app.command("reorder")
@guarded
def rule_reorder(
    ctx: typer.Context,
    rules: Annotated[str, typer.Argument(help="逗号分隔的完整规则顺序，如 r-a,r-b")],
    json_output: JsonOption = False,
):
    """拖拽后的规则排序写回 rules.yaml 并记审计。"""
    with owner(ctx) as facade:
        output(facade.reorder_rules([item.strip() for item in rules.split(",") if item.strip()]))


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


@engine_app.command("discover")
@guarded
def engine_discover(ctx: typer.Context, write: bool = False, json_output: JsonOption = False):
    from pa.engines.discovery import discover_local

    discovered = discover_local()
    if write:
        path = config_path(ctx) / "engines.yaml"
        config_path(ctx).mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = yaml.safe_load(path.read_text()) or {}
            merged = {**discovered.get("engines", {}), **existing.get("engines", {})}
            discovered = {"engines": merged, "roles": existing.get("roles") or discovered["roles"]}
        path.write_text(yaml.safe_dump(discovered, allow_unicode=True, sort_keys=False))
        load(config_path(ctx))
    output(
        {
            **discovered,
            "written": write,
            "gui_note": "豆包工作已发现，尚无可供 PA 无人值守调用的 GUI 驱动",
        }
    )


@engine_app.command("list")
@guarded
def engine_list(ctx: typer.Context, json_output: JsonOption = False):
    from pa.engines.registry import get_engine

    loaded = load(config_path(ctx))

    async def checks():
        result = []
        for id_, cfg in loaded.engines.engines.items():
            health = {
                "installed": bool(cfg.app_path and Path(cfg.app_path).exists()),
                "enabled": cfg.enabled,
                "reason": "GUI driver unavailable",
            }
            if cfg.enabled and cfg.adapter != "gui":
                health = await get_engine(loaded, id_).health()
            result.append({"id": id_, "adapter": cfg.adapter, **health})
        return result

    output({"items": asyncio.run(checks())})


@engine_app.command("test")
@guarded
def engine_test(
    ctx: typer.Context,
    id: str,
    text: str = "请用一句中文说明 2+2=4。",
    json_output: JsonOption = False,
):
    from pa.engines.base import EngineRequest
    from pa.engines.registry import get_engine
    from pa.skills.prompt_assembly import assemble

    loaded = load(config_path(ctx))
    request = EngineRequest(
        task_id=new_id(),
        prompt=assemble(text, policy_version=loaded.policy_version),
        output_dir=loaded.main.data_path(config_path(ctx)) / "engine-tests" / new_id(),
    )
    result = asyncio.run(get_engine(loaded, id).run(request))
    output(result.model_dump(mode="json"))
    if result.error:
        raise typer.Exit(2)


@engine_app.command("playbook")
@guarded
def engine_playbook(
    ctx: typer.Context,
    action: Annotated[str, typer.Argument(help="record 或 dry-run")],
    id: str,
    json_output: JsonOption = False,
):
    """剧本录制占位与试运行：dry-run 只打开不提交任何内容（M4-08）。"""
    from pa.engines.gui.driver import SyntheticDriver
    from pa.engines.gui.playbook import PlaybookRun, load_playbook

    if action == "record":
        output(
            {
                "engine_id": id,
                "recorded": False,
                "note": "录制需在 Mac mini 图形会话中进行（暂留）",
            }
        )
        return
    if action != "dry-run":
        raise typer.BadParameter("action 必须是 record 或 dry-run")

    playbook = load_playbook(config_path(ctx), id)
    run = PlaybookRun(playbook, SyntheticDriver())
    result = run.execute(prompt="试运行：不要提交任何内容", dry_run=True)
    output({"engine_id": id, **{k: v for k, v in result.items() if k != "log"}})
    if result["status"] != "succeeded":
        raise typer.Exit(2)


@secret_app.command("set")
@guarded
def secret_set(ref: str, json_output: JsonOption = False):
    from pa.obs.redact import redact
    from pa.secrets.keychain import KeychainProvider

    prefix = "secret://keychain/"
    if not ref.startswith(prefix) or not ref[len(prefix) :]:
        raise PAError("interactive secret set supports secret://keychain/path only")
    value = typer.prompt("输入密钥（不会显示）", hide_input=True, confirmation_prompt=True)
    if not value:
        raise PAError("secret cannot be empty")
    redact.register(value)
    KeychainProvider().set(ref[len(prefix) :], value)
    output({"ref": ref, "stored": True})


@secret_app.command("check")
@guarded
def secret_check(ref: str, json_output: JsonOption = False):
    from pa.secrets.resolver import resolve

    resolve(ref)
    output({"ref": ref, "readable": True})


@feishu_app.command("setup")
@guarded
def feishu_setup(
    ctx: typer.Context,
    app_id: str,
    owner_open_id: str | None = None,
    chat_id: str | None = None,
    json_output: JsonOption = False,
):
    from pa.config.setup import configure_feishu

    backup = configure_feishu(
        config_path(ctx), app_id=app_id, owner_open_id=owner_open_id, chat_id=chat_id
    )
    output(
        {
            "configured": True,
            "mode": "long_connection",
            "backup": str(backup),
            "paired": bool(load(config_path(ctx)).principals.owner.feishu_open_id),
        }
    )


@feishu_app.command("check")
@guarded
def feishu_check(ctx: typer.Context, json_output: JsonOption = False):
    from pa.notify.feishu import FeishuClient

    loaded = load(config_path(ctx))
    bot = asyncio.run(FeishuClient(loaded).bot_info())
    output(
        {
            "credentials_valid": True,
            "bot": bot,
            "paired": bool(loaded.principals.owner.feishu_open_id),
            "notification_target": bool(loaded.notify.target.id),
            "note": "凭证验证不代表平台已开启消息权限、事件订阅或完成发布。",
        }
    )


@feishu_app.command("pair")
@guarded
def feishu_pair(ctx: typer.Context, json_output: JsonOption = False):
    from pa.notify.pairing import create_pairing

    code = create_pairing(config_path(ctx))
    output(
        {
            "send_to_bot": "pa pair " + code,
            "expires_in_min": 30,
            "note": "由你本人在机器人私聊中发送；此口令只能使用一次。",
        }
    )


@feishu_app.command("listen")
@guarded
def feishu_listen(ctx: typer.Context):
    from pa.procs.feishu import listen

    listen(config_path(ctx))


@app.command("run")
@guarded
def run(
    ctx: typer.Context,
    once: bool = False,
    engine: str | None = None,
    json_output: JsonOption = False,
):
    """运行文本任务执行器、飞书收件进程和发件箱；Ctrl+C 停止。"""
    from pa.procs.runtime import serve

    output(asyncio.run(serve(config_path(ctx), once=once, engine_id=engine)))


@service_app.command("install")
@guarded
def service_install(ctx: typer.Context, start: bool = False, json_output: JsonOption = False):
    from pa.procs.service import install

    output(install(config_path(ctx), start=start))


@service_app.command("start")
@guarded
def service_start(json_output: JsonOption = False):
    from pa.procs.service import action

    output(action("start"))


@service_app.command("stop")
@guarded
def service_stop(json_output: JsonOption = False):
    from pa.procs.service import action

    output(action("stop"))


@service_app.command("status")
@guarded
def service_status(json_output: JsonOption = False):
    from pa.procs.service import action

    output(action("status"))


@skill_app.command("reorder")
@guarded
def skill_reorder(
    ctx: typer.Context,
    skills: Annotated[str, typer.Argument(help="逗号分隔的完整 Skill 顺序，如 a,b,c")],
    json_output: JsonOption = False,
):
    with owner(ctx) as facade:
        output(facade.reorder_skills([item.strip() for item in skills.split(",") if item.strip()]))


@skill_app.command("test")
@guarded
def skill_test(
    ctx: typer.Context,
    id: str,
    input_path: Annotated[
        str, typer.Option("--input", help="示例输入 JSON 文件（@file）或内联 JSON")
    ],
    json_output: JsonOption = False,
):
    """测试 Skill：返回命中的规则与将执行的流程，不实际执行。"""
    from pa.console.testing import dry_run_skill

    payload = _load_json_argument(input_path)
    output(dry_run_skill(config_path(ctx), id, payload))


# --- M5: breaker / archive / backup / cost / budget / memory / sandbox -----


@engine_app.command("breaker")
@guarded
def engine_breaker_reset(
    ctx: typer.Context,
    id: str,
    json_output: JsonOption = False,
):
    """重置引擎熔断器（owner 判断后人工重置）。"""
    from pa.obs.breaker import reset as breaker_reset

    with owner(ctx) as facade:
        with facade.engine.begin() as connection:
            output(breaker_reset(connection, id))


def _data_dir(ctx):
    with owner(ctx) as facade:
        loaded = load(config_path(ctx))
        return facade, loaded.main.data_path(config_path(ctx))


@archive_app.command("run")
@guarded
def archive_run(
    ctx: typer.Context,
    json_output: JsonOption = False,
):
    """执行一次快照归档并按 48 份本地保留清理。"""
    from pa.store.archive import prune_local, snapshot

    _, data_dir = _data_dir(ctx)
    target = snapshot(data_dir / "pa.sqlite3", data_dir / "snapshots")
    report = prune_local(data_dir / "snapshots")
    output({"snapshot": str(target), **report})


@archive_app.command("status")
@guarded
def archive_status(ctx: typer.Context, json_output: JsonOption = False):
    """查看本地快照列表与保留状态。"""
    _, data_dir = _data_dir(ctx)
    snapshots = sorted((data_dir / "snapshots").glob("pa-*.snapshot.db"))
    output({"count": len(snapshots), "latest": str(snapshots[-1]) if snapshots else None})


@backup_app.command("snapshot")
@guarded
def backup_snapshot(ctx: typer.Context, json_output: JsonOption = False):
    """立即创建一个 VACUUM INTO 快照。"""
    from pa.store.archive import snapshot as take_snapshot

    _, data_dir = _data_dir(ctx)
    output({"snapshot": str(take_snapshot(data_dir / "pa.sqlite3", data_dir / "snapshots"))})


@backup_app.command("restore")
@guarded
def backup_restore(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="快照文件路径")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: JsonOption = False,
):
    """从快照恢复数据库；--dry-run 只做完整性校验。"""
    from pa.store.archive import restore as restore_snapshot

    _, data_dir = _data_dir(ctx)
    output(
        restore_snapshot(Path(file).expanduser(), data_dir / "pa.sqlite3", dry_run=dry_run)
    )


@cost_app.command("summary")
@guarded
def cost_summary(
    ctx: typer.Context,
    by: Annotated[str, typer.Option("--by", help="skill | engine | day")] = "skill",
    json_output: JsonOption = False,
):
    """成本汇总（实际发生额，不含估算）。"""
    if by not in ("skill", "engine", "day"):
        raise typer.BadParameter("--by 必须是 skill、engine 或 day")
    column = {"skill": "skill_id", "engine": "engine_id", "day": "date(at)"}[by]
    with owner(ctx) as facade:
        with facade.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {column} AS bucket, COUNT(*) AS calls, "
                    "SUM(cost_cny) AS total_cny FROM cost_ledger "
                    f"WHERE est=0 GROUP BY {column} ORDER BY total_cny DESC LIMIT 200"
                )
            ).mappings()
            output({"by": by, "items": [dict(r) for r in rows]})


@budget_app.command("list")
@guarded
def budget_list(ctx: typer.Context, json_output: JsonOption = False):
    with owner(ctx) as facade:
        with facade.engine.connect() as connection:
            rows = connection.execute(text("SELECT * FROM budgets ORDER BY rowid")).mappings()
            output({"items": [dict(r) for r in rows]})


@budget_app.command("set")
@guarded
def budget_set(
    ctx: typer.Context,
    id: str,
    scope: Annotated[str, typer.Option(help="global | skill | engine | task")] = "skill",
    scope_id: Annotated[str | None, typer.Option()] = None,
    period: Annotated[str, typer.Option(help="day | month")] = "day",
    limit_cny: Annotated[float | None, typer.Option(help="null 只记账不拦截")] = None,
    soft_pct: Annotated[int, typer.Option(help="软提醒百分比")] = 80,
    json_output: JsonOption = False,
):
    """设置或更新一条预算。"""
    with owner(ctx) as facade:
        with facade.engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO budgets(id,scope,scope_id,period,limit_cny,soft_pct)
                VALUES(:id,:scope,:scope_id,:period,:limit,:soft)
                  ON CONFLICT(id) DO UPDATE SET scope=:scope,scope_id=:scope_id,
                    period=:period,limit_cny=:limit,soft_pct=:soft
            """),
                {
                    "id": id,
                    "scope": scope,
                    "scope_id": scope_id,
                    "period": period,
                    "limit": limit_cny,
                    "soft": soft_pct,
                },
            )
            row = connection.execute(
                text("SELECT * FROM budgets WHERE id=:id"), {"id": id}
            ).mappings().first()
            output(dict(row))


@memory_app.command("add")
@guarded
def memory_add(
    ctx: typer.Context,
    title: str,
    content: str,
    kind: Annotated[str, typer.Option(help="fact | lesson | context | profile")] = "fact",
    json_output: JsonOption = False,
):
    """新建一条记忆（写入前自动打码）。"""
    from pa.memory.store import add_memory

    with owner(ctx) as facade:
        with facade.engine.begin() as connection:
            output({"id": add_memory(connection, kind=kind, title=title, content=content)})


@memory_app.command("search")
@guarded
def memory_search(
    ctx: typer.Context,
    q: Annotated[str, typer.Argument(help="检索关键词")],
    json_output: JsonOption = False,
):
    """检索记忆库（vec → FTS5 → bigram 三级）。"""
    from pa.memory.store import retrieve

    with owner(ctx) as facade:
        with facade.engine.connect() as connection:
            output({"items": retrieve(connection, q)})


@memory_app.command("list")
@guarded
def memory_list(ctx: typer.Context, json_output: JsonOption = False):
    with owner(ctx) as facade:
        with facade.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM memories ORDER BY rowid DESC LIMIT 100")
            ).mappings()
            output({"items": [dict(r) for r in rows]})


@memory_app.command("delete")
@guarded
def memory_delete(ctx: typer.Context, id: str, json_output: JsonOption = False):
    with owner(ctx) as facade:
        with facade.engine.begin() as connection:
            result = connection.execute(text("DELETE FROM memories WHERE id=:id"), {"id": id})
            if result.rowcount == 0:
                raise PAError("memory not found", "not_found", 4)
            output({"deleted": id})


@sandbox_app.command("run")
@guarded
def sandbox_run(
    ctx: typer.Context,
    command: Annotated[
        str, typer.Argument(help="要执行的命令（shell 字符串），如 \"echo hi\"")
    ],
    timeout_min: Annotated[int, typer.Option(help="硬超时（分钟）")] = 15,
    json_output: JsonOption = False,
):
    """在一次性沙盒中运行命令（进程回退后端，网络关闭、不注入密钥）。"""
    from pa.sandbox.backends import select_backend
    from pa.sandbox.runtime import SandboxSpec, shell_command

    runtime = None
    sandbox_file = config_path(ctx) / "sandbox.yaml"
    if sandbox_file.exists():
        runtime = (yaml.safe_load(sandbox_file.read_text()) or {}).get("runtime")
    backend, name = select_backend(runtime)
    spec = SandboxSpec(command=shell_command(command), timeout_min=timeout_min)
    result = backend.run(spec)
    from dataclasses import asdict

    output({"backend": name, **asdict(result)})


@secret_app.command("rotate")
@guarded
def secret_rotate(
    ctx: typer.Context,
    ref: str,
    connectivity_ok: Annotated[bool, typer.Option(help="连通性测试是否通过")] = True,
    json_output: JsonOption = False,
):
    """登记一次密钥轮换（连通性通过才算完成）。"""
    from pa.secrets.rotation import complete_rotation

    with owner(ctx) as facade:
        with facade.engine.begin() as connection:
            output(complete_rotation(connection, ref, connectivity_ok=connectivity_ok))


@secret_app.command("list-meta")
@guarded
def secret_list_meta(ctx: typer.Context, json_output: JsonOption = False):
    """列出密钥轮换元数据（不含密钥值）。"""
    from pa.secrets.rotation import due

    with owner(ctx) as facade:
        with facade.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT ref,provider,rotated_at,rotate_every_days FROM secrets_meta")
            ).mappings()
            output({"items": [dict(r) for r in rows], "due": due(connection)})


@secret_app.command("register")
@guarded
def secret_register(
    ctx: typer.Context,
    ref: str,
    provider: Annotated[str, typer.Option(help="keychain | sops | 1password")] = "keychain",
    rotate_every_days: Annotated[int, typer.Option(help="轮换周期（天）")] = 30,
    json_output: JsonOption = False,
):
    """登记密钥的轮换元数据（只记元数据，不接触密钥值）。"""
    from pa.secrets.rotation import register

    with owner(ctx) as facade:
        with facade.engine.begin() as connection:
            register(
                connection, ref=ref, provider=provider, rotate_every_days=rotate_every_days
            )
            output({"ref": ref, "provider": provider, "rotate_every_days": rotate_every_days})
