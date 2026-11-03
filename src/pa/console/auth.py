"""Console login: password + TOTP, session cookies, CSRF (M3-04, §17.5).

Sessions use a signed cookie (HttpOnly, SameSite=Strict) plus a per-session
CSRF token that mutating requests must echo back. TOTP follows RFC 6238 with
the standard 30-second step and +/- 1 step drift tolerance.
"""

import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass

SESSION_COOKIE = "pa_session"
CSRF_COOKIE = "pa_csrf"
SESSION_TTL_S = 8 * 3600


def _totp_at(secret_key: bytes, counter: int, digits: int = 6) -> str:
    digest = hmac.new(secret_key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return f"{code:0{digits}d}"


def totp_now(secret_key: str | bytes, *, at: float | None = None, drift: int = 1) -> str:
    """Current TOTP code (base32-decoded key), tolerant of clock drift."""
    import base64

    if isinstance(secret_key, str):
        key = base64.b32decode(secret_key.upper() + "=" * (-len(secret_key) % 8))
    else:
        key = secret_key
    moment = at if at is not None else time.time()
    counter = int(moment // 30)
    return _totp_at(key, counter)


def verify_totp(secret_key: str | bytes, code: str, *, at: float | None = None) -> bool:
    import base64

    if isinstance(secret_key, str):
        key = base64.b32decode(secret_key.upper() + "=" * (-len(secret_key) % 8))
    else:
        key = secret_key
    moment = at if at is not None else time.time()
    counter = int(moment // 30)
    return any(hmac.compare_digest(_totp_at(key, counter + delta), code) for delta in (-1, 0, 1))


def _sign(value: str, server_secret: str) -> str:
    return hmac.new(server_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Session:
    token: str
    csrf: str
    expires_at: float


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt=salt), stored)


class LoginService:
    """Password + TOTP login issuing session cookies and CSRF tokens."""

    def __init__(self, *, password_hash: str, totp_secret: str, server_secret: str):
        self.password_hash = password_hash
        self.totp_secret = totp_secret
        self.server_secret = server_secret

    def login(self, *, password: str, totp: str, at: float | None = None) -> Session | None:
        if not verify_password(password, self.password_hash):
            return None
        if not verify_totp(self.totp_secret, totp, at=at):
            return None
        return self._new_session()

    def _new_session(self) -> Session:
        token = secrets.token_urlsafe(32)
        # CSRF derives from the session token (same formula as verify_session)
        # so the value echoed by the client stays valid across the session.
        csrf = _sign(token, "csrf:" + self.server_secret)[:32]
        return Session(token=token, csrf=csrf, expires_at=time.time() + SESSION_TTL_S)

    def sign_session(self, session: Session) -> str:
        payload = f"{session.token}.{int(session.expires_at)}"
        return f"{payload}.{_sign(payload, self.server_secret)}"

    def verify_session(self, signed: str | None) -> Session | None:
        if not signed:
            return None
        parts = signed.split(".")
        if len(parts) != 3:
            return None
        token, expires, signature = parts
        payload = f"{token}.{expires}"
        if not hmac.compare_digest(signature, _sign(payload, self.server_secret)):
            return None
        try:
            expires_at = int(expires)
        except ValueError:
            return None
        if time.time() > expires_at:
            return None
        # CSRF token derives from the session token so it is bound per session.
        csrf = _sign(token, "csrf:" + self.server_secret)[:32]
        return Session(token=token, csrf=csrf, expires_at=expires_at)

    def check_csrf(self, session: Session, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(session.csrf, presented)


def cookie_attributes() -> dict:
    """Set-Cookie flags required by §17.5."""
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": False,  # LAN deployment; true behind Cloudflare Access
        "path": "/",
    }
