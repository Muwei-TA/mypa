"""sops and 1Password secret backends (M5-12, PRD §17.2).

Both plug into pa.secrets via the same provider protocol as env/keychain:
load() returns a dict of path -> value. Switching default_provider in the
config swaps backends with no other code changes.
"""


class SopsProvider:
    """Decrypt an age-encrypted sops file and expose its keys."""

    name = "sops"

    def __init__(self, binary: str = "sops", file: str = "~/.config/pa/secrets.sops.yaml"):
        import shutil

        self.binary = shutil.which(binary) or binary
        self.file = file

    def available(self) -> bool:
        import shutil

        return shutil.which(self.binary) is not None

    def load(self) -> dict:
        import subprocess
        from pathlib import Path

        target = Path(self.file).expanduser()
        if not target.exists() or not self.available():
            return {}
        completed = subprocess.run(
            [self.binary, "-d", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return {}
        import yaml

        data = yaml.safe_load(completed.stdout) or {}
        return {k: v for k, v in data.items() if isinstance(v, str)}


class OnePasswordProvider:
    """Read items from the op CLI document store."""

    name = "1password"

    def __init__(self, binary: str = "op", vault: str = "PA"):
        import shutil

        self.binary = shutil.which(binary) or binary
        self.vault = vault

    def available(self) -> bool:
        import shutil

        return shutil.which(self.binary) is not None

    def load(self) -> dict:
        """Fetch every item field in the vault as a flat path -> value map."""
        import json
        import subprocess

        if not self.available():
            return {}
        completed = subprocess.run(
            [self.binary, "item", "list", "--vault", self.vault, "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return {}
        result: dict[str, str] = {}
        for item in json.loads(completed.stdout or "[]"):
            detail = subprocess.run(
                [self.binary, "item", "get", item["id"], "--format", "json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if detail.returncode != 0:
                continue
            data = json.loads(detail.stdout or "{}")
            for field in data.get("fields", []):
                value = field.get("value")
                if isinstance(value, str):
                    result[f"{item['title']}/{field.get('label', field.get('id', ''))}"] = value
        return result
