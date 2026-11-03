"""Upgrade the existing macOS PA service with config/runtime/SQLite rollback copies.

Uses the existing LaunchAgent, wrapper, secret references and installation path.
The wheel and locked dependencies are installed only after stopping the runtime.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def run(command, *, timeout=300):
    return subprocess.run(
        [str(v) for v in command], capture_output=True, text=True, check=True, timeout=timeout
    )


def database_copy(source, destination):
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    Path(destination).chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    repo = Path(__file__).resolve().parents[1]
    root = Path.home() / "Library/Application Support/PA"
    runtime, config = root / "runtime", root / "config"
    database = root / "data/pa.sqlite3"
    python = runtime / ".venv/bin/python"
    plist = Path.home() / "Library/LaunchAgents/com.pa.runtime.plist"
    if not wheel.is_file() or not python.is_file() or not plist.is_file():
        raise SystemExit("built wheel and existing PA installation required")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required")
    target = f"gui/{os.getuid()}/com.pa.runtime"
    backup = root / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-review-repair")
    backup.mkdir(parents=True, mode=0o700)
    requirements = backup / "requirements.lock.txt"
    run(
        [
            uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            requirements,
        ]
    )
    was_loaded = subprocess.run(["launchctl", "print", target], capture_output=True).returncode == 0
    if was_loaded:
        run(["launchctl", "bootout", target])
    # launchctl bootout waits for service exit, whose cleanup releases the core lock.
    time.sleep(1)
    shutil.copytree(config, backup / "config", symlinks=True)
    shutil.copy2(plist, backup / "com.pa.runtime.plist")
    shutil.copytree(runtime, backup / "runtime", symlinks=True)
    database_copy(database, backup / "pa.sqlite3")
    started = False
    try:
        run([uv, "pip", "sync", "--python", python, "--require-hashes", requirements])
        run([uv, "pip", "install", "--python", python, "--no-deps", wheel])
        run([python, "-m", "pa", "--config-dir", config, "migrate", "--json"])
        run([python, "-m", "pa", "--config-dir", config, "config", "validate", "--json"])
        manifest = {
            "wheel": wheel.name,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "installed_at": datetime.now(UTC).isoformat(),
            "backup": str(backup),
            "source_base": run(["git", "-C", repo, "rev-parse", "HEAD"]).stdout.strip(),
            "source_has_uncommitted_repairs": True,
        }
        (runtime / "installed-build.json").write_text(json.dumps(manifest, indent=2))
        if was_loaded:
            run(["launchctl", "bootstrap", f"gui/{os.getuid()}", plist])
            started = True
            # Require a new core heartbeat, not a stale success log entry.
            deadline = time.monotonic() + 30
            while True:
                with sqlite3.connect(database) as c:
                    row = c.execute(
                        "SELECT value,updated_at FROM system_state WHERE key='core_lock'"
                    ).fetchone()
                if row and datetime.fromisoformat(row[1]) > datetime.fromisoformat(
                    manifest["installed_at"]
                ):
                    manifest["core_heartbeat"] = row[1]
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("upgraded service did not produce a fresh heartbeat")
                time.sleep(0.5)
        status = run([python, "-m", "pa", "--config-dir", config, "service", "status", "--json"])
        manifest["service"] = json.loads(status.stdout)
        with sqlite3.connect(database) as c:
            manifest["schema"] = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            manifest["task_counts"] = dict(
                c.execute("SELECT status,count(*) FROM tasks GROUP BY status")
            )
        (backup / "upgrade-result.json").write_text(json.dumps(manifest, indent=2))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except BaseException as exc:
        diagnostic = {"error_type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, subprocess.CalledProcessError):
            diagnostic.update(
                {"command": exc.cmd, "returncode": exc.returncode, "stderr": exc.stderr}
            )
        failure_log = backup / "upgrade-error.json"
        failure_log.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        failure_log.chmod(0o600)
        if started:
            subprocess.run(["launchctl", "bootout", target], capture_output=True)
        # Preserve any new events received since restart before restoring the prior schema.
        database_copy(database, backup / "failed-upgrade.sqlite3")
        shutil.rmtree(runtime)
        shutil.copytree(backup / "runtime", runtime, symlinks=True)
        database_copy(backup / "pa.sqlite3", database)
        if was_loaded:
            run(["launchctl", "bootstrap", f"gui/{os.getuid()}", plist])
        raise RuntimeError(f"upgrade rolled back; evidence and recovery copies: {backup}") from None


if __name__ == "__main__":
    main()
