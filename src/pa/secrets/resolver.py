import os
import re
import stat
from pathlib import Path

from pa.common import PAError
from pa.obs.redact import redact


class EnvProvider:
    """Read-only environment backend. No implicit reading of user files."""

    def get(self, path: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path):
            raise PAError("env secret path must be a variable name")
        try:
            return os.environ[path]
        except KeyError:
            raise PAError("secret is unavailable", "not_found", 4) from None


def check_env_permissions(path: Path) -> None:
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PAError(".env must be a regular file with permissions 600", "forbidden", 3)
    if not path.is_file():
        raise PAError(".env must be a regular file", "forbidden", 3)


def resolve(ref: str) -> str:
    from pa.plugins import discover

    if not ref.startswith("secret://"):
        raise PAError("expected a secret reference")
    provider, separator, path = ref[len("secret://") :].partition("/")
    if not separator or not path:
        raise PAError("invalid secret reference")
    providers = discover("pa.secrets")
    if provider not in providers:
        raise PAError(f"secret provider is not installed: {provider}")
    value = providers[provider].load()().get(path)
    redact.register(value)
    return value
