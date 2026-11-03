import json

from sqlalchemy import text

from pa.common import now
from pa.notify.dispatch import backfill_recovered, record_feishu_failure, record_feishu_success
from pa.notify.feishu import FeishuClient
from pa.store.db import transaction


class FeishuHandler:
    kind = "feishu.send"

    def __init__(self, config, engine, *, client=None, local_notifier=None):
        self.client = client or FeishuClient(config)
        self.engine = engine
        self.local_notifier = local_notifier

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
        try:
            result = await self.client.send(json.loads(effect["payload_json"]), effect["idem_key"])
        except Exception:
            with transaction(self.engine) as c:
                await record_feishu_failure(c, effect=effect, notifier=self.local_notifier)
            raise
        with transaction(self.engine) as c:
            record_feishu_success(c)
            backfill_recovered(c, executed=[effect["id"]])
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
