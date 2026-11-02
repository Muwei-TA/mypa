import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.effects.runner import EffectRunner
from pa.engines.process import ENV_ALLOW
from pa.notify.dispatch import flush_due
from pa.plugins import discover
from pa.procs.worker import Worker
from pa.store.db import transaction
from pa.store.facades import OwnerFacade


async def serve(config_dir, *, once=False, engine_id=None):
    import os

    facade = OwnerFacade(config_dir)
    lock_owner = new_id()
    child = None
    log_file = None

    def lock_value():
        return json.dumps(
            {"owner": lock_owner, "until": (datetime.now(UTC) + timedelta(seconds=40)).isoformat()}
        )

    with transaction(facade.engine) as c:
        current = c.execute(
            text("SELECT value FROM system_state WHERE key='core_lock'")
        ).scalar_one_or_none()
        if current and json.loads(current)["until"] > now():
            facade.close()
            raise PAError("another PA runtime holds the core lock", "conflict")
        c.execute(
            text("""
            INSERT INTO system_state(key,value,updated_at) VALUES('core_lock',:value,:at)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """),
            {"value": lock_value(), "at": now()},
        )

    parent = asyncio.current_task()

    async def pulse():
        while True:
            await asyncio.sleep(10)
            with transaction(facade.engine) as c:
                result = c.execute(
                    text("""
                    UPDATE system_state SET value=:value,updated_at=:at
                    WHERE key='core_lock' AND json_extract(value,'$.owner')=:owner
                """),
                    {"value": lock_value(), "at": now(), "owner": lock_owner},
                )
                if not result.rowcount:
                    parent.cancel()
                    return

    pulse_task = asyncio.create_task(pulse())
    try:
        cfg = load(config_dir)
        if not once and cfg.sources.sources.feishu.enabled:
            logs = cfg.main.data_path(config_dir) / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            log_file = (logs / "feishu.log").open("a")
            (logs / "feishu.log").chmod(0o600)
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pa.procs.feishu",
                str(config_dir),
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
                env={k: v for k, v in os.environ.items() if k in ENV_ALLOW},
            )
        while True:
            cfg = load(config_dir)
            if child and child.returncode is not None:
                raise PAError("Feishu receiver exited; inspect .pa-local/data/logs/feishu.log")
            task_result = await Worker(facade, config_dir).once(engine_id)
            handlers = {}
            if cfg.sources.sources.feishu.enabled:
                handlers["feishu.send"] = discover("pa.effects")["feishu.send"].load()(
                    cfg, facade.engine
                )
            with transaction(facade.engine) as c:
                flush_due(c, config=cfg)
            delivery = await EffectRunner(facade.engine, handlers).once()
            if once:
                return {"task": task_result, "effect": delivery}
            if task_result is None and delivery is None:
                await asyncio.sleep(1)
    finally:
        pulse_task.cancel()
        await asyncio.gather(pulse_task, return_exceptions=True)
        if child and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 5)
            except TimeoutError:
                child.kill()
                await child.wait()
        if log_file:
            log_file.close()
        with transaction(facade.engine) as c:
            c.execute(
                text(
                    "DELETE FROM system_state WHERE key='core_lock' "
                    "AND json_extract(value,'$.owner')=:owner"
                ),
                {"owner": lock_owner},
            )
        facade.close()
