"""M1-16 backfill executor and M1-19 effect/rule CLI regressions."""

import asyncio
import json

import pytest
import yaml
from sqlalchemy import text

from pa.common import PAError, now
from pa.effects.feishu import FeishuHandler
from pa.ingress.manual import ManualAdapter
from pa.notify.dispatch import (
    BACKFILL_KEY,
    _set_state,
    backfill_recovered,
    request_notification,
)
from pa.notify.levels import P1
from pa.store.db import transaction


class RecordingClient:
    """Feishu stand-in: fails until `revive()` is called, then succeeds."""

    def __init__(self):
        self.calls = []
        self.down = True

    def revive(self):
        self.down = False

    async def send(self, payload, idem_key):
        if self.down:
            raise RuntimeError("network down")
        self.calls.append(idem_key)
        return {"message_id": "om_" + str(len(self.calls))}

    async def message_exists(self, message_id):
        return True


def _state(connection, key):
    return connection.execute(
        text("SELECT value FROM system_state WHERE key=:k"), {"k": key}
    ).scalar_one_or_none()


def _effects(facade):
    return {item["id"]: item for item in facade.list("effects")["items"]}


def _enqueue_p1(facade, text_="需要决定"):
    with transaction(facade.engine) as c:
        return request_notification(c, config=facade.config, task_id="t1", level=P1, text_=text_)[
            "effect_id"
        ]


def _mark_backfill(facade, effect_id, status, attempts):
    with transaction(facade.engine) as c:
        c.execute(
            text("UPDATE effects SET status=:status,attempts=:attempts WHERE id=:id"),
            {"id": effect_id, "status": status, "attempts": attempts},
        )
        _set_state(c, BACKFILL_KEY, json.dumps([{"effect_id": effect_id, "at": now()}]))


def test_backfill_requeues_retryable_failed_after_recovery(configured):
    effect_id = _enqueue_p1(configured, "失败后补发")
    _mark_backfill(configured, effect_id, "failed", 3)

    with transaction(configured.engine) as c:
        out = backfill_recovered(c)
    assert out["requeued"] == 1 and out["remaining"] == 0
    effects = _effects(configured)
    assert effects[effect_id]["status"] == "pending"
    assert effects[effect_id]["next_at"] is None
    with configured.engine.connect() as c:
        assert _state(c, BACKFILL_KEY) is None


def test_backfill_keeps_marker_for_inflight_effects(configured):
    effect_id = _enqueue_p1(configured)
    _mark_backfill(configured, effect_id, "running", 1)

    with transaction(configured.engine) as c:
        out = backfill_recovered(c)
    assert out == {"requeued": 0, "remaining": 1, "ids": []}
    with configured.engine.connect() as c:
        assert json.loads(_state(c, BACKFILL_KEY))[0]["effect_id"] == effect_id


def test_backfill_clears_marker_for_done_effects(configured):
    effect_id = _enqueue_p1(configured)
    _mark_backfill(configured, effect_id, "done", 1)

    with transaction(configured.engine) as c:
        out = backfill_recovered(c)
    assert out["requeued"] == 0 and out["remaining"] == 0
    with configured.engine.connect() as c:
        assert _state(c, BACKFILL_KEY) is None


def test_handler_success_requeues_pending_backfill(configured):
    client = RecordingClient()
    handler = FeishuHandler(configured.config, configured.engine, client=client)
    failed_id = _enqueue_p1(configured, "故障期间消息")
    fresh_id = _enqueue_p1(configured, "恢复后消息")
    _mark_backfill(configured, failed_id, "failed", 2)

    # The recovery send succeeds; the failed marker must requeue automatically.
    client.revive()
    asyncio.run(handler.execute(_effects(configured)[fresh_id]))
    with configured.engine.connect() as c:
        assert _state(c, BACKFILL_KEY) is None
    assert _effects(configured)[failed_id]["status"] == "pending"

    asyncio.run(handler.execute(_effects(configured)[failed_id]))
    assert client.calls, "backfilled effect was not delivered after recovery"


def test_effect_retry_and_drop_cli_methods(configured):
    effect_id = _enqueue_p1(configured)
    _mark_backfill(configured, effect_id, "failed", 4)

    out = configured.effect_retry(effect_id)
    assert out == {"effect_id": effect_id, "status": "pending", "attempts": 4}
    assert _effects(configured)[effect_id]["status"] == "pending"

    with pytest.raises(PAError):
        configured.effect_retry("missing")
    configured.effect_drop(effect_id)
    assert _effects(configured)[effect_id]["status"] == "dropped"
    with pytest.raises(PAError):
        configured.effect_drop(effect_id)
    audit = configured.list("audit_log")["items"]
    assert any(a["action"] == "effect.retry" for a in audit)
    assert any(a["action"] == "effect.drop" for a in audit)


def test_rule_mode_persists_and_audits(configured, config_dir):
    out = configured.set_rule_mode("r-manual-direct", "observe")
    assert out["changed"] is True
    persisted = yaml.safe_load((config_dir / "rules.yaml").read_text())
    target = next(r for r in persisted["rules"] if r["id"] == "r-manual-direct")
    assert target["accept_mode"] == "observe"

    again = configured.set_rule_mode("r-manual-direct", "observe")
    assert again["changed"] is False

    with pytest.raises(PAError):
        configured.set_rule_mode("r-manual-direct", "invalid")
    with pytest.raises(PAError):
        configured.set_rule_mode("missing", "auto")

    audit = configured.list("audit_log")["items"]
    assert any(a["action"] == "rule.mode" for a in audit)


def test_rule_mode_keeps_explicit_manual_requests(configured, config_dir):
    """Manual source is explicit: observe mode must not block it (M1-09 rule)."""
    configured.set_rule_mode("r-manual-direct", "observe")
    event = ManualAdapter().parse(
        {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "hello"}}
    )
    result = configured.emit(event)
    assert result["tasks"], "explicit manual request must not be blocked by observe mode"
