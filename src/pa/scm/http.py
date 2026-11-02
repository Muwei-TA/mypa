"""Shared HTTP plumbing for platform adapters: identity, retry and rate limits."""

import asyncio
import hashlib
import hmac
import secrets
import time
from typing import Any

import httpx


class ScmError(Exception):
    """transient (retry) | permanent (drop) | auth (re-dispatch, no identity switch)."""

    def __init__(self, message: str, kind: str = "transient", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


class Identity:
    """agent or merger credentials (PRD §10.1 identity separation)."""

    def __init__(self, name: str, token: str | None, headers: dict[str, str] | None = None):
        self.name = name
        self.token = token
        self.headers = headers or {}


def hmac_sha256(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_hmac(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac_sha256(secret, body)
    return hmac.compare_digest(expected, signature)


class Client:
    """Thin httpx wrapper: transient on 5xx/network, permanent on 4xx."""

    def __init__(self, base_url: str, identities: dict[str, Identity] | None = None):
        self.base_url = base_url.rstrip("/")
        self.identities = identities or {}
        self._client: httpx.AsyncClient | None = None

    def identity(self, name: str) -> Identity:
        identity = self.identities.get(name)
        if identity is None:
            raise ScmError(f"identity {name} is not configured", "auth")
        return identity

    async def request(
        self,
        method: str,
        path: str,
        *,
        identity: str = "agent",
        json_body: dict | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        # v1.1·D17: merges only ever run under the merger identity.
        if identity == "agent" and method in ("PUT", "POST") and path.endswith("/merge"):
            raise ScmError("agent identity must not merge (D17)", "permanent")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        ident = self.identity(identity)
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            **ident.headers,
        }
        if ident.token:
            headers["Authorization"] = f"Bearer {ident.token}"
        # Transient errors retry once on a fresh connection before surfacing.
        last: Exception | None = None
        for _ in range(2):
            try:
                response = await self._client.request(
                    method, url, headers=headers, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                last = exc
                await asyncio.sleep(0.1)
                continue
            if response.status_code >= 500:
                last = ScmError(f"{method} {path} -> {response.status_code}", "transient")
                await asyncio.sleep(0.1)
                continue
            if response.status_code in (401, 403):
                raise ScmError(f"{method} {path} -> {response.status_code}", "auth")
            if response.status_code >= 400:
                raise ScmError(
                    f"{method} {path} -> {response.status_code}", "permanent", response.status_code
                )
            return response.json() if response.content else {}
        raise last or ScmError(f"{method} {path} failed", "transient")


def install_token_refresher(client: Client, refresher):
    """GitHub App installation tokens auto-refresh; called before each request."""

    async def wrapped(method, path, **kwargs):
        await refresher()
        return await client.request(method, path, **kwargs)

    client.request = wrapped  # type: ignore[method-assign]


class RateLimiter:
    """Simple token bucket shared per platform (adapted to httpx sync tests)."""

    def __init__(self, rate: float = 10.0):
        self.rate = rate
        self._tokens = rate
        self._last = time.monotonic()

    def allow(self) -> bool:
        now_ = time.monotonic()
        self._tokens = min(self.rate, self._tokens + (now_ - self._last) * self.rate)
        self._last = now_
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


def delivery_id(headers: dict[str, str]) -> str | None:
    for key in ("X-GitHub-Delivery", "X-Gitea-Delivery"):
        if key in headers:
            return headers[key]
    return secrets.token_urlsafe(8)
