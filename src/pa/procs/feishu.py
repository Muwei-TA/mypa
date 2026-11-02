import asyncio
import json
import sys
from pathlib import Path

from pa.common import now
from pa.config.loader import load
from pa.hub.pause import set_value
from pa.ingress.feishu import parse_message
from pa.notify.feishu import FeishuClient
from pa.notify.pairing import try_pair
from pa.secrets.resolver import resolve
from pa.store.db import transaction
from pa.store.facades import OwnerFacade


def listen(config_dir: Path):
    import lark_oapi as lark

    config = load(config_dir)
    source = config.sources.sources.feishu
    bot = asyncio.run(FeishuClient(config).bot_info())

    def receive(data):
        raw = json.loads(lark.JSON.marshal(data))
        facade = OwnerFacade(config_dir)
        try:
            content = json.loads(raw["event"]["message"].get("content", "{}"))
            if content.get("text", "").startswith("pa pair "):
                try_pair(raw, config_dir, facade)
                return
            facade.emit(parse_message(raw, facade.config, bot_open_id=bot.get("open_id")))
        finally:
            facade.close()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(receive)
        .build()
    )

    def connection_state(connected):
        facade = OwnerFacade(config_dir)
        try:
            with transaction(facade.engine) as c:
                set_value(c, "feishu_connection", json.dumps({"connected": connected, "at": now()}))
        finally:
            facade.close()

    class ObservedClient(lark.ws.Client):
        async def _connect(self):
            await super()._connect()
            connection_state(True)

        async def _disconnect(self):
            connection_state(False)
            await super()._disconnect()

    client = ObservedClient(
        source.app_id,
        resolve(source.app_secret),
        event_handler=handler,
        log_level=lark.LogLevel.ERROR,
        domain=FeishuClient(config).domain,
    )
    from pa.obs.logging import configure_sdk_logging

    configure_sdk_logging()
    # lark-oapi owns a synchronous event loop. Keep it in its own receiver process.
    client.start()


if __name__ == "__main__":
    listen(Path(sys.argv[1]))
