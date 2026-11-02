import re
from collections.abc import Mapping


class Redactor:
    def __init__(self):
        self._known: set[str] = set()
        self._patterns = [
            re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
            re.compile(r"\b(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]+"),
            re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
            re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
            re.compile(r"(?i)(?:authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+"),
            re.compile(r"(?i)(?<=://)[^/\s:@]+:[^/\s@]+@"),
        ]

    def register(self, secret: str) -> None:
        if secret:
            self._known.add(secret)

    def __call__(self, value):
        if isinstance(value, str):
            for secret in sorted(self._known, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            for pattern in self._patterns:
                value = pattern.sub("[REDACTED]", value)
            return value
        if isinstance(value, Mapping):
            return {
                self(str(k)): "[REDACTED]"
                if str(k).lower() in {"authorization", "cookie", "set-cookie", "api_key", "token"}
                else self(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self(v) for v in value]
        return value


redact = Redactor()
