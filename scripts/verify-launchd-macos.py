"""Real launchd crash/restart drill in a separate disposable PA instance.

Uses the installed PA wheel and a harmless scripted CLI engine. This tests
launchd/SQLite/leases, not a real provider or a whole-machine power failure.
"""

import json
import os
import plistlib
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pa.config.loader import initialize
from pa.config.setup import save_files
from pa.ingress.manual import ManualAdapter
from pa.store.db import migrate
from pa.store.facades import OwnerFacade


def main():
    if sys.platform != "darwin":
        raise SystemExit("macOS required")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path.home() / "Library/Application Support/PA/verification" / f"launchd-{stamp}"
    root.mkdir(parents=True, mode=0o700)
    config = root / "config"
    initialize(config)
    engine = root / "fake_engine.py"
    shutil.copy2(Path(__file__).resolve().parents[1] / "tests/e2e/fake_engine.py", engine)
    save_files(
        config,
        {
            "engines.yaml": {
                "engines": {
                    "fake": {
                        "adapter": "codex_cli",
                        "command": [sys.executable, str(engine)],
                        "timeout_s": 5,
                    }
                },
                "roles": {"dev": ["fake"]},
            }
        },
    )
    facade = OwnerFacade(config)
    migrate(facade.engine)
    task = facade.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "slow launchd verification"},
            }
        )
    )["tasks"][0]
    facade.close()
    label = "com.pa.verification." + stamp.lower()
    domain = f"gui/{os.getuid()}"
    target = domain + "/" + label
    plist = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": [
                    sys.executable,
                    "-m",
                    "pa",
                    "--config-dir",
                    str(config),
                    "run",
                ],
                "WorkingDirectory": str(root),
                "KeepAlive": True,
                "RunAtLoad": True,
                "ThrottleInterval": 5,
                "Umask": 63,
                "EnvironmentVariables": {
                    "HOME": str(Path.home()),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                },
                "StandardOutPath": str(root / "runtime.log"),
                "StandardErrorPath": str(root / "runtime-error.log"),
            }
        )
    )
    plist.chmod(0o600)

    def rows(query):
        with sqlite3.connect(root / "data/pa.sqlite3") as db:
            return db.execute(query).fetchall()

    def pid():
        result = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
        match = re.search(r"\bpid = (\d+)", result.stdout)
        return int(match[1]) if match else None

    def wait(check, timeout):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            value = check()
            if value:
                return value
            time.sleep(0.25)
        raise RuntimeError("launchd drill timed out; inspect " + str(root))

    try:
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True
        )
        wait(lambda: rows("SELECT 1 FROM runs WHERE started_at IS NOT NULL"), 15)
        original = wait(pid, 5)
        killed_at = time.monotonic()
        os.kill(original, signal.SIGKILL)
        print(
            json.dumps({"stage": "killed", "pid": original, "label": label, "root": str(root)}),
            flush=True,
        )
        wait(lambda: rows("SELECT 1 FROM tasks WHERE status='closed'"), 150)
        recovered_pid = wait(pid, 5)
        runs = rows("SELECT attempt,recovered,outcome FROM runs ORDER BY attempt")
        effects = rows("SELECT idem_key,count(*) FROM effects GROUP BY idem_key HAVING count(*)>1")
        assert recovered_pid != original and len(runs) >= 2
        assert len({r[0] for r in runs}) == len(runs) and not effects
        report = {
            "task_id": task,
            "label": label,
            "old_pid": original,
            "new_pid": recovered_pid,
            "status": "closed",
            "recovery_seconds": round(time.monotonic() - killed_at, 3),
            "runs": runs,
            "duplicate_effects": effects,
            "data": str(root),
            "engine": "scripted, no external effects",
        }
        (root / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        subprocess.run(["launchctl", "bootout", target], capture_output=True)
        plist.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
