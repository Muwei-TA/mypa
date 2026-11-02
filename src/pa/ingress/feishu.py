import json
from datetime import UTC, datetime

from pa.ingress.base import Event


def parse_message(data: dict, config, *, bot_open_id=None) -> Event:
    body = data["event"]
    message = body["message"]
    sender = body["sender"]
    actor = sender.get("sender_id", {}).get("open_id")
    allowed = sender.get("sender_type") == "user"
    group = message.get("chat_type") == "group"
    if group:
        target = config.notify.target
        allowed = allowed and target.type == "chat" and message.get("chat_id") == target.id
    mentions = message.get("mentions") or []
    addressed = not group or bool(
        bot_open_id
        and any(mention.get("id", {}).get("open_id") == bot_open_id for mention in mentions)
    )
    content = json.loads(message.get("content", "{}"))
    if group and not addressed:
        allowed = False
    text = content.get("text", "") if message.get("message_type") == "text" else ""
    if not text:
        allowed = False
    for mention in mentions:
        if bot_open_id and mention.get("id", {}).get("open_id") == bot_open_id:
            text = text.replace(mention.get("key", "@_bot"), "").strip()
    return Event(
        source="feishu",
        type="feishu.message",
        category="manual",
        actor=f"feishu:{actor}" if allowed else None,
        subject=message.get("root_id") or message["message_id"],
        payload={
            "skill": "sample-direct",
            "text": text,
            "chat_id": message.get("chat_id"),
            "message_id": message["message_id"],
        },
        dedup_key=message["message_id"],
        received_at=datetime.now(UTC),
        trust="verified",
        summary=text[:80] or "不支持的消息类型",
        addressed=addressed,
        threads=["feishu:" + message["message_id"]],
    )
