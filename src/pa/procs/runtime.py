import asyncio
import json
import signal
import sys
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.effects.runner import EffectRunner
from pa.engines.process import ENV_ALLOW
from pa.hub.pause import set_value
from pa.notify.dispatch import flush_due
from pa.plugins import discover
from pa.procs.worker import Worker
from pa.store.db import transaction
from pa.store.facades import OwnerFacade


async def run_cycles(worker_once, delivery_once, stop, *, receiver_check=None, interval=1):
    """Keep delivery and receiver supervision responsive while a model turn runs."""

    async def repeat(operation):
        while not stop.is_set():
            await operation()
            await asyncio.sleep(interval)

    tasks = [asyncio.create_task(repeat(worker_once)), asyncio.create_task(repeat(delivery_once))]
    if receiver_check:
        tasks.append(asyncio.create_task(repeat(receiver_check)))
    stopped = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        stop.set()
        for task in [*tasks, stopped]:
            task.cancel()
        await asyncio.gather(*tasks, stopped, return_exceptions=True)


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
    termination_requested = False
    previous_sigterm = None
    loop = asyncio.get_running_loop()

    def terminate():
        nonlocal termination_requested
        termination_requested = True
        parent.cancel()

    if not once and sys.platform != "win32":
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        loop.add_signal_handler(signal.SIGTERM, terminate)

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

    def pulse_failed(task):
        if not task.cancelled() and task.exception() is not None:
            parent.cancel()

    pulse_task.add_done_callback(pulse_failed)
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
                start_new_session=False,
                env={k: v for k, v in os.environ.items() if k in ENV_ALLOW},
            )

        async def worker_once():
            return await Worker(facade, config_dir).once(engine_id)

        async def receiver_check():
            if child and child.returncode is not None:
                raise PAError(f"Feishu receiver exited; inspect {logs / 'feishu.log'}")

        async def delivery_once():
            cfg = load(config_dir)
            handlers = {}
            if cfg.sources.sources.feishu.enabled:
                handlers["feishu.send"] = discover("pa.effects")["feishu.send"].load()(
                    cfg, facade.engine
                )
            with transaction(facade.engine) as c:
                flush_due(c, config=cfg)
            return await EffectRunner(facade.engine, handlers).once()

        if once:
            return {"task": await worker_once(), "effect": await delivery_once()}
        structlog.get_logger().info("runtime_started", owner=lock_owner)
        await run_cycles(worker_once, delivery_once, asyncio.Event(), receiver_check=receiver_check)
    except asyncio.CancelledError:
        if termination_requested:
            return {"stopped": True}
        raise
    finally:
        if previous_sigterm is not None:
            loop.remove_signal_handler(signal.SIGTERM)
            signal.signal(signal.SIGTERM, previous_sigterm)
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
            if child:
                set_value(c, "feishu_connection", json.dumps({"connected": False, "at": now()}))
            c.execute(
                text(
                    "DELETE FROM system_state WHERE key='core_lock' "
                    "AND json_extract(value,'$.owner')=:owner"
                ),
                {"owner": lock_owner},
            )
        facade.close()
