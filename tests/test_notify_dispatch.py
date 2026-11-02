import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from pa.common import now
from pa.effects.feishu import FeishuHandler
from pa.engines.base import EngineResult
from pa.ingress.manual import ManualAdapter
from pa.notify.dispatch import (
    BUFFER_KEY,
    FAILURE_KEY,
    WINDOW_S,
    backfill_pending,
    buffer_p2,
    flush_due,
    record_feishu_failure,
    record_feishu_success,
    request_notification,
)
from pa.notify.feishu import target_payload
from pa.notify.levels import P1, P2
from pa.procs.worker import Worker
from pa.skills.receipt import Receipt, Usage
from pa.store.db import transaction


class FakeNotifier:
    def __init__(self):
        self.calls = []

    async def display(self, title, message):
        self.calls.append((title, message))


class GoodEngine:
    async def health(self):
        return {"installed": True}

    async def run(self, request):
        return EngineResult(
            engine_id="fake",
            receipt=Receipt(
                status="succeeded",
                summary="完成",
                code_changed=False,
                confidence=0.9,
                risk="low",
                evidence=["输入"],
                usage=Usage(),
            ),
        )


class FailingClient:
    async def send(self, *args):
        raise RuntimeError("network down")


def manual():
    return ManualAdapter().parse(
        {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "概括这段"}}
    )


def _state(connection, key):
    return connection.execute(
        text("SELECT value FROM system_state WHERE key=:k"), {"k": key}
    ).scalar_one_or_none()


def _set_state(connection, key, value):
    connection.execute(
        text("""
        INSERT INTO system_state(key,value,updated_at) VALUES(:k,:v,:a)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """),
        {"k": key, "v": value, "a": now()},
    )


def _effect(config, id_="fx"):
    return {
        "id": id_,
        "payload_json": json.dumps(target_payload(config, {"text": "重要通知"})),
    }


def test_p1_enqueues_immediately(configured):
    with transaction(configured.engine) as c:
        out = request_notification(
            c, config=configured.config, task_id="t1", level=P1, text_="需要处理"
        )
    assert "effect_id" in out
    assert len(configured.list("effects")["items"]) == 1


def test_p2_buffers_instead_of_outbox(configured):
    with transaction(configured.engine) as c:
        out = request_notification(
            c, config=configured.config, task_id="t1", level=P2, text_="结案1"
        )
    assert out == {"buffered": True, "level": "P2"}
    assert configured.list("effects")["items"] == []
    with configured.engine.connect() as c:
        assert len(json.loads(_state(c, BUFFER_KEY))["items"]) == 1


def test_flush_not_due_inside_window(configured):
    with transaction(configured.engine) as c:
        buffer_p2(c, task_id="t1", text_="x")
        assert flush_due(c, config=configured.config) is None


def test_flush_after_window_merges_text(configured):
    with transaction(configured.engine) as c:
        buffer_p2(c, task_id="t1", text_="a")
        buffer_p2(c, task_id="t2", text_="b")
        aged = json.loads(_state(c, BUFFER_KEY))
        aged["window_started_at"] = (
            datetime.now(UTC) - timedelta(seconds=WINDOW_S + 1)
        ).isoformat()
        _set_state(c, BUFFER_KEY, json.dumps(aged, ensure_ascii=False))
        flushed = flush_due(c, config=configured.config)
    assert flushed["items"] == 2
    effect = configured.list("effects")["items"][0]
    assert json.loads(effect["payload_json"])["content"]["text"] == "a\nb"
    with configured.engine.connect() as c:
        assert _state(c, BUFFER_KEY) is None


def test_force_flush_merges_three(configured):
    with transaction(configured.engine) as c:
        for i in range(3):
            buffer_p2(c, task_id=f"t{i}", text_=f"卡{i}")
        flushed = flush_due(c, config=configured.config, force=True)
    assert flushed["items"] == 3
    text_ = json.loads(configured.list("effects")["items"][0]["payload_json"])
    assert text_["content"]["text"] == "卡0\n卡1\n卡2"


def test_first_failure_starts_window_without_local(configured):
    with transaction(configured.engine) as c:
        result = asyncio.run(
            record_feishu_failure(c, effect=_effect(configured.config), notifier=FakeNotifier())
        )
    assert result is None
    with configured.engine.connect() as c:
        assert _state(c, FAILURE_KEY) is not None


def test_failure_under_threshold_keeps_silent(configured):
    notifier = FakeNotifier()
    with transaction(configured.engine) as c:
        _set_state(
            c,
            FAILURE_KEY,
            (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        )
        result = asyncio.run(
            record_feishu_failure(c, effect=_effect(configured.config), notifier=notifier)
        )
    assert result is None and notifier.calls == []


def test_failure_threshold_triggers_local_and_backfill(configured):
    notifier = FakeNotifier()
    with transaction(configured.engine) as c:
        _set_state(
            c,
            FAILURE_KEY,
            (datetime.now(UTC) - timedelta(minutes=16)).isoformat(),
        )
        result = asyncio.run(
            record_feishu_failure(c, effect=_effect(configured.config), notifier=notifier)
        )
    assert result == {"local_fallback": True, "effect_id": "fx"}
    assert notifier.calls == [("PA 飞书不可用", "重要通知")]
    with configured.engine.connect() as c:
        pending = backfill_pending(c)
        assert pending[0]["effect_id"] == "fx"
        assert datetime.now(UTC) - datetime.fromisoformat(_state(c, FAILURE_KEY)) < timedelta(
            seconds=5
        )


def test_success_clears_failure_window(configured):
    with transaction(configured.engine) as c:
        _set_state(c, FAILURE_KEY, now())
        record_feishu_success(c)
    with configured.engine.connect() as c:
        assert _state(c, FAILURE_KEY) is None


def test_handler_failure_invokes_local_fallback(configured):
    notifier = FakeNotifier()
    with transaction(configured.engine) as c:
        buffer_p2(c, task_id="t1", text_="x")
        flush_due(c, config=configured.config, force=True)
    effect = configured.list("effects")["items"][0]
    with transaction(configured.engine) as c:
        _set_state(
            c,
            FAILURE_KEY,
            (datetime.now(UTC) - timedelta(minutes=16)).isoformat(),
        )
    handler = FeishuHandler(
        configured.config,
        configured.engine,
        client=FailingClient(),
        local_notifier=notifier,
    )
    with pytest.raises(RuntimeError):
        asyncio.run(handler.execute(effect))
    assert notifier.calls


def test_decision_only_sends_no_result_card(configured, config_dir):
    # Default rule r-manual-direct keeps notify=decision_only.
    configured.emit(manual())
    result = asyncio.run(Worker(configured, config_dir, engines={"fake": GoodEngine()}).once())
    assert result["status"] == "closed"
    assert configured.list("effects")["items"] == []
    with configured.engine.connect() as c:
        assert _state(c, BUFFER_KEY) is None
