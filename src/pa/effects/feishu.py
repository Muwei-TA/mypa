import json

from sqlalchemy import text

from pa.common import now
from pa.notify.feishu import FeishuClient
from pa.store.db import transaction


class FeishuHandler:
    kind = "feishu.send"

    def __init__(self, config, engine, *, client=None):
        self.client = client or FeishuClient(config)
        self.engine = engine

    async def already_done(self, effect):
        with self.engine.connect() as c:
            message_id = c.execute(
                text("SELECT value FROM system_state WHERE key=:key"),
                {
                    "key": "effect_message:" + effect["id"],
                },
            ).scalar_one_or_none()
        if not message_id:
            return False
        return await self.client.message_exists(message_id)

    async def execute(self, effect):
        result = await self.client.send(json.loads(effect["payload_json"]), effect["idem_key"])
        with transaction(self.engine) as c:
            c.execute(
                text("""
                INSERT INTO system_state(key,value,updated_at) VALUES(:key,:value,:at)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
            """),
                {
                    "key": "effect_message:" + effect["id"],
                    "value": result["message_id"],
                    "at": now(),
                },
            )
        return {"message_id": result["message_id"]}
