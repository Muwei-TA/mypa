"""Notification routing: levels, P2 merge window, Feishu failure fallback.

Implements PRD §12.2/§12.3:

* P0/P1 cards are sent immediately; P2 small cards merge inside a 10-minute
  window; P3 is reserved for the daily summary.
* When Feishu delivery keeps failing for 15 minutes, a macOS notification is
  shown locally and the effect is marked for later backfill.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import new_id, now
from pa.effects.store import enqueue
from pa.notify.feishu import target_payload
from pa.notify.levels import P0, P1, P2
from pa.notify.local import default_notifier

WINDOW_S = 600
FAILURE_S = 900
BUFFER_KEY = "notify_p2_buffer"
FAILURE_KEY = "feishu_failure_since"
BACKFILL_KEY = "feishu_backfill"


def _get_state(connection, key):
    return connection.execute(
        text("SELECT value FROM system_state WHERE key=:key"), {"key": key}
    ).scalar_one_or_none()


def _set_state(connection, key, value):
    connection.execute(
        text("""
        INSERT INTO system_state(key,value,updated_at) VALUES(:key,:value,:at)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """),
        {"key": key, "value": value, "at": now()},
    )


def _send_now(connection, config, *, task_id, text_, idem_suffix):
    return enqueue(
        connection,
        task_id=task_id,
        kind="feishu.send",
        idem_key=f"{task_id or 'sys'}:notify:{idem_suffix}",
        payload=target_payload(config, {"text": text_}),
        order_key="feishu:" + config.notify.target.id,
    )


def request_notification(connection, *, config, task_id, level, text_):
    """Route one notification: P2 buffers; P0/P1 enter the outbox directly."""
    if level == P2:
        buffer_p2(connection, task_id=task_id, text_=text_)
        return {"buffered": True, "level": level}
    if level not in (P0, P1):
        raise ValueError(f"level {level} does not dispatch immediately")
    effect_id = _send_now(connection, config, task_id=task_id, text_=text_, idem_suffix=new_id())
    return {"effect_id": effect_id, "level": level}


def buffer_p2(connection, *, task_id, text_):
    raw = _get_state(connection, BUFFER_KEY)
    if raw:
        buffer = json.loads(raw)
    else:
        buffer = {"window_started_at": now(), "items": []}
    buffer["items"].append({"task_id": task_id, "text": text_})
    _set_state(connection, BUFFER_KEY, json.dumps(buffer, ensure_ascii=False))


def flush_due(connection, *, config, force=False):
    """Flush the P2 buffer once its window elapsed (or when forced)."""
    raw = _get_state(connection, BUFFER_KEY)
    if not raw:
        return None
    buffer = json.loads(raw)
    started = datetime.fromisoformat(buffer["window_started_at"])
    if not force and datetime.now(UTC) - started < timedelta(seconds=WINDOW_S):
        return None
    merged = "\n".join(item["text"] for item in buffer["items"])
    effect_id = _send_now(
        connection,
        config,
        task_id=buffer["items"][0]["task_id"],
        text_=merged,
        idem_suffix="p2:" + buffer["window_started_at"],
    )
    connection.execute(text("DELETE FROM system_state WHERE key=:key"), {"key": BUFFER_KEY})
    return {"effect_id": effect_id, "items": len(buffer["items"])}


def record_feishu_success(connection):
    connection.execute(text("DELETE FROM system_state WHERE key=:key"), {"key": FAILURE_KEY})


def backfill_pending(connection):
    raw = _get_state(connection, BACKFILL_KEY)
    return json.loads(raw) if raw else []


async def record_feishu_failure(connection, *, effect, notifier=None):
    """Track consecutive Feishu failures; fall back locally after 15 minutes."""
    raw = _get_state(connection, FAILURE_KEY)
    if not raw:
        _set_state(connection, FAILURE_KEY, now())
        return None
    since = datetime.fromisoformat(raw)
    if datetime.now(UTC) - since < timedelta(seconds=FAILURE_S):
        return None
    notifier = notifier or default_notifier()
    payload = json.loads(effect["payload_json"])
    message = payload.get("content", {}).get("text", "飞书通知发送失败")
    await notifier.display("PA 飞书不可用", message)
    pending = backfill_pending(connection)
    pending.append({"effect_id": effect["id"], "at": now()})
    _set_state(connection, BACKFILL_KEY, json.dumps(pending, ensure_ascii=False))
    # Restart the window so a persistent outage notifies at most once per 15 min.
    _set_state(connection, FAILURE_KEY, now())
    return {"local_fallback": True, "effect_id": effect["id"]}
