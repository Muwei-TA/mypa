"""Config versioning and conflict detection (M3-03, PRD §19.3 / v1.1·D15).

Every save creates an immutable version row in ``config_versions`` plus a git
commit when the config dir is a repository. Writes must carry
``base_revision``; a stale base returns 409 and the draft stays with the
caller (never partially applied). Overrides identical to defaults are
dropped automatically.
"""

import hashlib
import subprocess
from pathlib import Path

import yaml
from sqlalchemy import text

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.config.setup import save_files

VERSION_FILES = frozenset(
    {
        "pa.yaml",
        "rules.yaml",
        "principals.yaml",
        "engines.yaml",
        "sources.yaml",
        "notify.yaml",
        "schedules.yaml",
        "merge_policy.yaml",
    }
)


class ConfigConflict(PAError):
    """409: the base revision is stale; the draft is preserved."""

    def __init__(self, message, expected_revision):
        super().__init__(message, "config_conflict", 409)
        self.expected_revision = expected_revision


def content_hash(value: dict) -> str:
    canonical = yaml.safe_dump(value, allow_unicode=True, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def current_revision(connection, file_name: str) -> int:
    row = connection.execute(
        text("""
        SELECT revision FROM config_versions WHERE file=:file
        ORDER BY revision DESC LIMIT 1
    """),
        {"file": file_name},
    ).scalar_one_or_none()
    return row or 0


def _git(config_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(config_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def save_versioned(
    connection,
    config_dir: Path,
    *,
    file_name: str,
    value: dict,
    base_revision: int,
    origin: str = "console",
    actor: str = "owner",
):
    """Validate -> compare base -> write file -> version row -> git commit.

    On base mismatch raises ConfigConflict (draft untouched). When the new
    content hash equals the previous one the write is a no-op (same-value
    override auto-removal applies upstream of this call).
    """
    if file_name not in VERSION_FILES:
        raise PAError("file is not versioned config", "validation_error", 2)
    current = current_revision(connection, file_name)
    if current != base_revision:
        raise ConfigConflict(
            f"config {file_name} moved from revision {base_revision} to {current}",
            current,
        )
    # Validate the whole staged snapshot before touching real files.
    staged = config_dir / (".stage-" + new_id())
    staged.mkdir()
    try:
        for source in config_dir.glob("*.yaml"):
            if source.name != file_name:
                (staged / source.name).write_bytes(source.read_bytes())
        (staged / file_name).write_text(
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        )
        load(staged)
    finally:
        for item in staged.iterdir():
            item.unlink()
        staged.rmdir()
    digest = content_hash(value)
    previous = connection.execute(
        text("""
        SELECT content_hash FROM config_versions WHERE file=:file
        ORDER BY revision DESC LIMIT 1
    """),
        {"file": file_name},
    ).scalar_one_or_none()
    if previous is not None and digest == previous:
        # Same-content saves are no-ops (D15: identical overrides auto-drop).
        return {
            "file": file_name,
            "revision": current,
            "content_hash": digest,
            "unchanged": True,
        }
    save_files(config_dir, {file_name: value})
    revision = current + 1
    connection.execute(
        text("""
        INSERT INTO config_versions(revision,file,content_hash,base_revision,origin,actor,at)
        VALUES(:rev,:file,:hash,:base,:origin,:actor,:at)
    """),
        {
            "rev": revision,
            "file": file_name,
            "hash": digest,
            "base": base_revision,
            "origin": origin,
            "actor": actor,
            "at": now(),
        },
    )
    _maybe_git_commit(config_dir, file_name, revision, actor)
    return {
        "file": file_name,
        "revision": revision,
        "content_hash": digest,
        "unchanged": False,
    }


def _maybe_git_commit(config_dir: Path, file_name: str, revision: int, actor: str) -> None:
    """Commit the change when the config dir is already a git repository."""
    if not (config_dir / ".git").exists():
        return
    _git(config_dir, "add", file_name)
    _git(
        config_dir,
        "-c",
        f"user.name={actor}",
        "-c",
        f"user.email={actor}@pa.local",
        "commit",
        "-m",
        f"config: {file_name} r{revision}",
    )


def versions_of(connection, file_name: str) -> list[dict]:
    rows = connection.execute(
        text("""
        SELECT revision,content_hash,base_revision,origin,actor,at
        FROM config_versions WHERE file=:file ORDER BY revision DESC
    """),
        {"file": file_name},
    ).all()
    return [
        {
            "revision": row[0],
            "content_hash": row[1],
            "base_revision": row[2],
            "origin": row[3],
            "actor": row[4],
            "at": row[5],
        }
        for row in rows
    ]


def drop_same_as_default(connection, config_dir: Path, *, file_name: str, default: dict):
    """An override identical to the default is removed; future defaults flow."""
    if file_name not in VERSION_FILES:
        raise PAError("file is not versioned config", "validation_error", 2)
    target = config_dir / file_name
    if not target.exists():
        return {"removed": False}
    current = yaml.safe_load(target.read_text()) or {}
    if content_hash(current) != content_hash(default):
        return {"removed": False}
    base = current_revision(connection, file_name)
    target.unlink()
    revision = base + 1
    connection.execute(
        text("""
        INSERT INTO config_versions(revision,file,content_hash,base_revision,origin,actor,at)
        VALUES(:rev,:file,NULL,:base,'upgrade','system',:at)
    """),
        {"rev": revision, "file": file_name, "base": base, "at": now()},
    )
    _maybe_git_commit(config_dir, file_name, revision, "system")
    return {"removed": True, "revision": revision}
