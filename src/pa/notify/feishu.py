import json
import time
import uuid

import httpx

from pa.common import PAError
from pa.obs.redact import redact
from pa.secrets.resolver import resolve


class FeishuError(Exception):
    def __init__(self, code: str, permanent=False):
        super().__init__(f"Feishu request failed ({code})")
        self.permanent = permanent


class FeishuClient:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.transport = transport
        self._token = None
        self._expires = 0.0

    @property
    def domain(self):
        return {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}[
            self.config.sources.sources.feishu.brand]

    async def request(self, method, path, *, body=None, params=None, authenticated=True):
        headers = {}
        if authenticated:
            headers["Authorization"] = "Bearer " + await self.token()
        async with httpx.AsyncClient(
            base_url=self.domain,
            transport=self.transport,
            timeout=20,
            follow_redirects=False,
        ) as client:
            try:
                response = await client.request(
                    method, path, json=body, params=params, headers=headers
                )
            except httpx.HTTPError:
                raise FeishuError("network") from None
        if response.status_code != 200:
            raise FeishuError(str(response.status_code), response.status_code in (400, 403, 404))
        try:
            data = response.json()
        except ValueError:
            raise FeishuError("invalid_response") from None
        if data.get("code") != 0:
            code = data.get("code", "missing_code")
            if code in (99991663, 99991668):
                self._expires = 0
            raise FeishuError(str(code), code in (230001, 230002, 230013, 230027, 99991672))
        return data

    async def token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        cfg = self.config.sources.sources.feishu
        if not cfg.enabled or not cfg.app_id:
            raise PAError("Feishu is not configured and enabled")
        data = await self.request(
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            authenticated=False,
            body={"app_id": cfg.app_id, "app_secret": resolve(cfg.app_secret)},
        )
        self._token = data["tenant_access_token"]
        redact.register(self._token)
        self._expires = time.monotonic() + max(0, int(data.get("expire", 7200)) - 60)
        return self._token

    async def bot_info(self):
        return (await self.request("GET", "/open-apis/bot/v3/info"))["bot"]

    async def send(self, payload, idem_key):
        # Payload has already passed the outbox boundary's redactor.
        body = {
            "receive_id": payload["receive_id"],
            "msg_type": payload.get("msg_type", "text"),
            "content": json.dumps(redact(payload["content"]), ensure_ascii=False),
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, idem_key)),
        }
        result = await self.request(
            "POST",
            "/open-apis/im/v1/messages",
            body=body,
            params={"receive_id_type": payload["receive_id_type"]},
        )
        return result["data"]

    async def message_exists(self, message_id):
        await self.request("GET", "/open-apis/im/v1/messages/" + message_id)
        return True


def target_payload(config, content, *, msg_type="text"):
    target = config.notify.target
    if not target.id:
        raise PAError("notification target is missing")
    return {
        "receive_id": target.id,
        "receive_id_type": "open_id" if target.type == "user" else "chat_id",
        "msg_type": msg_type,
        "content": content,
    }
