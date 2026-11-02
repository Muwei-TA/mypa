import secrets
import time
from datetime import UTC, datetime


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def new_id() -> str:
    """ULID: 48-bit millisecond timestamp followed by 80 random bits."""
    value = (time.time_ns() // 1_000_000 << 80) | secrets.randbits(80)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(alphabet[(value >> shift) & 31] for shift in range(125, -1, -5))


class PAError(Exception):
    def __init__(self, message: str, code: str = "bad_request", exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
