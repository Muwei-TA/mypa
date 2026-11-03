#!/usr/bin/env python3
"""Interactively store Gitea sandbox service PATs on this Mac (never in Git).

Only the human entering the tokens sees the local terminal prompt. The script
never prints token material or asks ChatGPT to handle credentials.
"""

import argparse
import getpass
import json
import os
import stat
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

API = "https://git.muwei.xyz/api/v1"
SANDBOX = "/repos/muwei/pa-v11-acceptance-sandbox"
PRODUCTION = "/repos/muwei/pa"
DEST = Path.home() / "Library" / "Application Support" / "PA-acceptance-secrets"
ACCOUNTS = {"agent": "pa-agent-e2e", "merger": "pa-merger-e2e"}


class SetupError(Exception):
    pass


def _get(token: str, resource: str) -> dict | None:
    req = urllib.request.Request(
        API + resource,
        headers={"Authorization": "token " + token, "Accept": "application/json"},
    )
    try:
        # Gitea is only contacted over verified HTTPS, without ambient proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=12) as r:
            data = json.load(r)
        if not isinstance(data, dict):
            raise SetupError("Gitea returned an unexpected payload")
        return data
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        if exc.code == 401:
            raise SetupError("Gitea refused this token (HTTP 401)") from None
        raise SetupError(f"Gitea HTTP {exc.code} while validating access") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SetupError(f"Cannot reach Gitea securely: {type(exc).__name__}") from None


def validate(token: str, expected: str) -> None:
    if not token or len(token) < 25 or any(ch.isspace() for ch in token):
        raise SetupError("Missing or malformed token")
    identity = _get(token, "/user")
    if not identity or identity.get("login") != expected:
        raise SetupError(f"Token must authenticate as {expected}; include read:user scope")
    if identity.get("is_admin"):
        raise SetupError("An administrator token is forbidden for sandbox automation")
    target = _get(token, SANDBOX)
    permissions = target.get("permissions", {}) if target else {}
    if not permissions.get("pull") or not permissions.get("push") or permissions.get("admin"):
        raise SetupError("Account must have sandbox Write access but not Admin access")
    production = _get(token, PRODUCTION)
    if production and (production.get("permissions") or {}).get("push"):
        raise SetupError("This account has production write access: refusing to store token")
    if production and (production.get("permissions") or {}).get("admin"):
        raise SetupError("This account has production Admin access: refusing to store token")


def store(token: str, role: str, folder: Path = DEST) -> None:
    if folder.is_symlink():
        raise SetupError("Secret folder must not be a symlink")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    target = folder / f"{role}.token"
    if target.exists() or target.is_symlink():
        raise SetupError(f"{role}.token already exists; refusing to overwrite")
    # mkstemp creates mode 0600. Atomic rename prevents partial secret files.
    fd, temp = tempfile.mkstemp(prefix=f".{role}.", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
        os.chmod(temp, 0o600)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def check_saved(role: str, folder: Path = DEST) -> None:
    if folder.is_symlink() or not folder.is_dir() or stat.S_IMODE(folder.stat().st_mode) != 0o700:
        raise SetupError("Secret folder must be a real directory with permissions 0700")
    file = folder / f"{role}.token"
    if file.is_symlink() or not file.is_file() or stat.S_IMODE(file.stat().st_mode) != 0o600:
        raise SetupError(f"{role} token must be a regular file with permissions 0600")
    validate(file.read_text().strip(), ACCOUNTS[role])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify existing tokens without prompting"
    )
    args = parser.parse_args()
    try:
        for role, account in ACCOUNTS.items():
            target = DEST / f"{role}.token"
            if target.exists() or args.check:
                check_saved(role)
                print(f"{account}: authenticated; sandbox write only; local secret permissions OK")
                continue
            token = getpass.getpass(f"Paste Gitea PAT for {account} (input hidden): ").strip()
            validate(token, account)
            store(token, role)
            print(f"{account}: authenticated and securely stored")
        print("Sandbox credential setup complete. Do not share tokens in chat or Git.")
        return 0
    except (SetupError, OSError) as exc:
        print(f"Setup not complete: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
