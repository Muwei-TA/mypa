"""Behavioral regressions found while reviewing b565dd9..d7162d2."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from pa.api.app import create_app
from pa.common import new_id
from pa.engines.base import EngineResult
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.receipt import Receipt, Usage
from pa.store.db import transaction

TOKEN = "review-owner-test-token"


def manual():
    return ManualAdapter().parse(
        {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "概括文本"}}
    )


def success():
    return EngineResult(
        engine_id="fake",
        usage=Usage(input_tokens=5, output_tokens=2),
        receipt=Receipt(
            status="succeeded",
            summary="完成",
            code_changed=False,
            confidence=1.0,
            risk="low",
            evidence=[],
            usage=Usage(),
        ),
    )


async def no_sleep(_):
    pass


def test_default_api_does_not_grant_owner(facade):
    client = TestClient(create_app(facade))
    assert client.get("/healthz").status_code == 200
    assert client.post("/api/v1/system:pause").status_code == 401
    assert not facade.status()["paused"]


@pytest.mark.parametrize("body", [{}, {"type": "manual.test", "payload": []}])
def test_invalid_event_request_is_422_not_500(facade, body):
    client = TestClient(create_app(facade, api_token=TOKEN), raise_server_exceptions=False)
    result = client.post("/api/v1/events", json=body, headers={"Authorization": "Bearer " + TOKEN})
    assert result.status_code == 422


def test_task_filter_is_applied_before_limit(facade):
    wanted = new_id()
    with transaction(facade.engine) as c:
        c.execute(
            text(
                "INSERT INTO tasks(id,status,priority,skill_id) "
                "VALUES(:id,'queued','P2','sample-direct')"
            ),
            {"id": wanted},
        )
        for _ in range(501):
            c.execute(
                text("INSERT INTO tasks(id,status,priority) VALUES(:id,'closed','P2')"),
                {"id": new_id()},
            )
    client = TestClient(create_app(facade, api_token=TOKEN))
    result = client.get("/api/v1/tasks?status=queued", headers={"Authorization": "Bearer " + TOKEN})
    assert [item["id"] for item in result.json()["items"]] == [wanted]


@pytest.mark.parametrize("kind", ["transient", "invalid_output"])
def test_owner_cancel_prevents_more_engine_calls(configured, config_dir, kind):
    task_id = configured.emit(manual())["tasks"][0]

    class Engine:
        calls = 0

        async def health(self):
            return {"installed": True}

        async def run(self, request):
            self.calls += 1
            if self.calls == 1:
                configured.cancel(task_id)
            return EngineResult(engine_id="fake", error="simulated", error_kind=kind)

    engine = Engine()
    result = asyncio.run(
        Worker(configured, config_dir, engines={"fake": engine}, sleeper=no_sleep).once()
    )
    assert result["status"] == "cancelled"
    assert engine.calls == 1


def test_receipt_repair_usage_is_accounted(configured, config_dir):
    task_id = configured.emit(manual())["tasks"][0]

    class Engine:
        calls = 0

        async def health(self):
            return {"installed": True}

        async def run(self, request):
            self.calls += 1
            if self.calls == 1:
                return EngineResult(
                    engine_id="fake",
                    error="bad receipt",
                    error_kind="invalid_output",
                    usage=Usage(input_tokens=11, output_tokens=3),
                )
            return success()

    assert (
        asyncio.run(Worker(configured, config_dir, engines={"fake": Engine()}).once())["status"]
        == "closed"
    )
    with configured.engine.connect() as c:
        row = c.execute(
            text("SELECT sum(input_tokens),sum(output_tokens) FROM cost_ledger WHERE task_id=:id"),
            {"id": task_id},
        ).one()
    assert tuple(row) == (16, 5)


def test_retry_exhaustion_notifies_owner(configured, config_dir):
    configured.emit(manual())

    class Engine:
        async def health(self):
            return {"installed": True}

        async def run(self, request):
            return EngineResult(engine_id="fake", error="offline", error_kind="transient")

    worker = Worker(configured, config_dir, engines={"fake": Engine()}, sleeper=no_sleep)
    for _ in range(3):
        result = asyncio.run(worker.once())
    assert result["status"] == "awaiting_decision"
    assert len(configured.list("effects")["items"]) == 1
