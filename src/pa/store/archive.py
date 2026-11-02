"""Snapshot archive and restore (M5-10, PRD §13.3/§22.6).

Hourly VACUUM INTO snapshots uploaded to WebDAV (rclone on the host), 48
kept locally. restore --dry-run verifies the snapshot integrity without
touching the live database.
"""

import sqlite3
from pathlib import Path

LOCAL_KEEP = 48


def snapshot(db_path: Path, target_dir: Path) -> Path:
    """Create a consistent snapshot via VACUUM INTO."""
    target_dir.mkdir(parents=True, exist_ok=True)
    from pa.common import now

    stamp = now().replace(":", "").replace("-", "")[:13]  # 20260101T00
    target = target_dir / f"pa-{stamp}.snapshot.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()
    return target


def prune_local(target_dir: Path, keep: int = LOCAL_KEEP) -> dict:
    """Keep only the newest `keep` snapshots locally."""
    files = sorted(target_dir.glob("pa-*.snapshot.db"))
    removed = 0
    for stale in files[:-keep] if len(files) > keep else []:
        stale.unlink()
        removed += 1
    return {"kept": min(len(files), keep), "removed": removed}


def verify(snapshot_path: Path) -> dict:
    """Dry-run restore check: open the snapshot and count core tables."""
    if not snapshot_path.exists():
        return {"ok": False, "error": "snapshot file missing"}
    conn = sqlite3.connect(str(snapshot_path))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"events", "tasks", "task_events", "runs", "effects", "decisions"}
        missing = required - tables
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in sorted(required & tables)
        }
    finally:
        conn.close()
    return {"ok": not missing, "missing": sorted(missing), "counts": counts}


def restore(snapshot_path: Path, live_path: Path, *, dry_run: bool = True) -> dict:
    """Restore a snapshot over the live db; dry-run only verifies."""
    check = verify(snapshot_path)
    if dry_run or not check["ok"]:
        return {"mode": "dry-run" if dry_run else "aborted", **check}
    if live_path.exists():
        live_path.unlink()
    snapshot_path.replace(live_path)
    return {"mode": "restored", **check}
