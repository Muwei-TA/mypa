import asyncio
import json
import select
import subprocess
import sys

import pytest
from sqlalchemy import text

from pa.common import PAError
from pa.config.setup import save_files
from pa.ingress.manual import ManualAdapter
from pa.obs.redact import Redactor
from pa.procs.runtime import run_cycles
from pa.procs.worker import Worker


def test_websocket_url_credentials_are_redacted():
    raw = "wss://example.invalid/ws?ticket=temporary-private-value&service_id=123"
    redacted = Redactor()(raw)
    assert "temporary-private-value" not in redacted
    assert "service_id=123" in redacted


def test_delivery_does_not_wait_for_model_completion():
    async def scenario():
        started, delivered, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
        model_finished = False

        async def worker():
            nonlocal model_finished
            started.set()
            await asyncio.Future()
            model_finished = True

        async def delivery():
            await started.wait()
            delivered.set()
            stop.set()

        await asyncio.wait_for(run_cycles(worker, delivery, stop, interval=0.01), timeout=1)
        assert delivered.is_set() and not model_finished

    asyncio.run(scenario())


def test_cycle_failure_cancels_siblings():
    async def scenario():
        cleaned = asyncio.Event()
        started = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        async def delivery():
            await started.wait()
            raise PAError("simulated delivery process failure")

        with pytest.raises(PAError):
            await asyncio.wait_for(run_cycles(worker, delivery, asyncio.Event()), timeout=1)
        assert cleaned.is_set()

    asyncio.run(scenario())


def test_external_worker_cancellation_propagates(configured, config_dir):
    configured.emit(
        ManualAdapter().parse(
            {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "test"}}
        )
    )

    async def scenario():
        started = asyncio.Event()

        class Engine:
            async def health(self):
                return {"installed": True}

            async def run(self, request):
                started.set()
                await asyncio.Future()

        pending = asyncio.create_task(
            Worker(configured, config_dir, engines={"fake": Engine()}).once()
        )
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
    assert configured.list("effects")["items"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal lifecycle")
def test_sigterm_releases_runtime_lock(facade, config_dir):
    # No queued task and no Feishu credentials: this child cannot call a model or send messages.
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {"fake": {"adapter": "codex_cli", "command": [sys.executable]}},
                "roles": {"dev": ["fake"]},
            }
        },
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "pa", "--config-dir", str(config_dir), "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "runtime did not become ready"
        ready = json.loads(process.stdout.readline())
        assert ready["event"] == "runtime_started"
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["stopped"] is True
        with facade.engine.connect() as c:
            assert (
                c.execute(
                    text("SELECT count(*) FROM system_state WHERE key='core_lock'")
                ).scalar_one()
                == 0
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
