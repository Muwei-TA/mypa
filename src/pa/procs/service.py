import os
import plistlib
import subprocess
import sys
from pathlib import Path

from pa.common import PAError
from pa.config.loader import load

LABEL = "com.pa.runtime"


def install(config_dir, *, start=False, replace=False):
    config = load(config_dir)
    data = config.main.data_path(config_dir)
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    destination = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    arguments = [sys.executable, "-m", "pa", "--config-dir", str(config_dir), "run"]
    if destination.exists():
        old = plistlib.loads(destination.read_bytes())
        if old.get("ProgramArguments") != arguments and not replace:
            raise PAError("an existing PA service uses a different installation", "conflict")
    document = {
        "Label": LABEL,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(data),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "Umask": 63,
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(Path.home()),
        },
        "StandardOutPath": str(logs / "runtime.log"),
        "StandardErrorPath": str(logs / "runtime-error.log"),
    }
    destination.write_bytes(plistlib.dumps(document))
    destination.chmod(0o600)
    if start:
        action("start")
    return {"plist": str(destination), "started": start}


def action(verb):
    domain = f"gui/{os.getuid()}"
    target = domain + "/" + LABEL
    path = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    if verb == "start":
        command = ["launchctl", "bootstrap", domain, str(path)]
    elif verb == "stop":
        command = ["launchctl", "bootout", target]
    elif verb == "status":
        command = ["launchctl", "print", target]
    else:
        raise PAError("unsupported service action")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    if verb != "status" and result.returncode:
        raise PAError("launchctl operation failed; check service status")
    if verb == "status":
        lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().startswith(("state =", "pid =", "last exit code ="))
        ]
        return {"loaded": result.returncode == 0, "details": lines}
    return {"ok": True, "action": verb}
