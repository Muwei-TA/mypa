"""Bounded public HTTP(S) source reader. No credentials, scripts or implicit proxy."""

import asyncio
import fnmatch
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from pa.common import PAError
from pa.obs.redact import redact


class SourceError(PAError):
    def __init__(self, message):
        super().__init__(message, "source_read_failed")


class TextExtractor(HTMLParser):
    HIDDEN = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@dataclass(frozen=True)
class Source:
    url: str
    text: str


async def resolve_public(host, port):
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in answers))
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            raise SourceError("source resolves to a non-public address")
        return addresses[0]
    except OSError:
        raise SourceError("source hostname could not be resolved") from None


def validate_url(value, permissions):
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, TypeError):
        raise SourceError("invalid source URL") from None
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.port not in {None, 80, 443}
        or redact(str(url)) != str(url)
    ):
        raise SourceError("source URL must be public HTTP(S) without credentials")
    if permissions.network == "none":
        raise SourceError("Skill network access is disabled")
    if permissions.network == "allowlist" and not any(
        fnmatch.fnmatchcase(url.host.lower(), pattern.lower())
        for pattern in permissions.network_allow
    ):
        raise SourceError("source hostname is outside the Skill allowlist")
    try:
        if not ipaddress.ip_address(url.host).is_global:
            raise SourceError("source address is not public")
    except ValueError:
        pass  # A hostname is resolved and checked before connecting.
    return url.copy_with(fragment=None)


class ReadURL:
    name = "read_url"

    def __init__(
        self,
        *,
        transport=None,
        resolver=None,
        max_bytes=1_048_576,
        max_chars=100_000,
        timeout_s=20,
        max_redirects=3,
    ):
        self.transport = transport
        self.resolver = resolver or resolve_public
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.timeout_s = timeout_s
        self.max_redirects = max_redirects

    async def read(self, value, *, permissions):
        try:
            async with asyncio.timeout(self.timeout_s):
                return await self._read(value, permissions)
        except (TimeoutError, httpx.TimeoutException):
            raise SourceError("source read timed out") from None
        except httpx.HTTPError:
            raise SourceError("source HTTP request failed") from None

    async def _read(self, value, permissions):
        current = validate_url(value, permissions)
        for hop in range(self.max_redirects + 1):
            ip = await self.resolver(
                current.host, current.port or (443 if current.scheme == "https" else 80)
            )
            if not ipaddress.ip_address(ip).is_global:
                raise SourceError("source address is not public")
            # Connect only to the address that was checked. Preserve the original
            # Host/SNI for virtual hosting and TLS certificate validation.
            pinned = current.copy_with(host=ip)
            async with httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(self.timeout_s),
            ) as client:
                async with client.stream(
                    "GET",
                    pinned,
                    headers={
                        "Host": current.netloc.decode("ascii"),
                        "Accept-Encoding": "identity",
                        "User-Agent": "PA/0.1 source-reader",
                    },
                    extensions={"sni_hostname": current.host},
                ) as response:
                    if response.is_redirect:
                        if hop == self.max_redirects or not response.headers.get("location"):
                            raise SourceError("source redirect limit exceeded")
                        destination = validate_url(
                            str(current.join(response.headers["location"])), permissions
                        )
                        if current.scheme == "https" and destination.scheme != "https":
                            raise SourceError("source redirects to insecure HTTP")
                        current = destination
                        continue
                    if not 200 <= response.status_code < 300:
                        raise SourceError("source returned an unsuccessful HTTP status")
                    media = (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if not (
                        media.startswith("text/")
                        or media in {"application/json", "application/xhtml+xml"}
                    ):
                        raise SourceError("source is not a supported text document")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise SourceError("compressed source responses are unsupported")
                    content = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(content) + len(chunk) > self.max_bytes:
                            raise SourceError("source exceeds the byte limit")
                        content.extend(chunk)
                    decoded = bytes(content).decode(response.encoding or "utf-8", errors="replace")
                    if media in {"text/html", "application/xhtml+xml"}:
                        parser = TextExtractor()
                        parser.feed(decoded)
                        decoded = " ".join(" ".join(parser.parts).split())
                    if not decoded.strip():
                        raise SourceError("source contains no readable text")
                    if len(decoded) > self.max_chars:
                        raise SourceError("source exceeds the text limit")
                    return Source(url=str(current), text=decoded)
        raise SourceError("source redirect limit exceeded")
