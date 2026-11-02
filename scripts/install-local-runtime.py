"""Install the built wheel and locked runtime dependencies outside Documents on macOS.

Run after stopping the existing PA service. No credentials are copied: configs hold secret refs.
"""

import shlex
import shutil
import sqlite3
import subprocess
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
home = Path.home()
destination = home / "Library/Application Support/PA"
source_config = repo / ".pa-local/config"
source_data = repo / ".pa-local/data"
uv = shutil.which("uv")
if not uv:
    raise SystemExit("uv is required")
if (destination / "runtime").exists():
    raise SystemExit("Runtime already exists; use an explicit upgrade instead of overwriting it")
destination.mkdir(parents=True, exist_ok=True, mode=0o700)
runtime = destination / "runtime"
runtime.mkdir(mode=0o700)
requirements = runtime / "requirements.lock.txt"
subprocess.run(
    [
        uv,
        "export",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--format",
        "requirements-txt",
        "--output-file",
        str(requirements),
    ],
    cwd=repo,
    check=True,
)
subprocess.run([uv, "venv", str(runtime / ".venv"), "--python", "3.12"], check=True)
python = runtime / ".venv/bin/python"
subprocess.run(
    [uv, "pip", "install", "--python", str(python), "--require-hashes", "-r", str(requirements)],
    check=True,
)
wheel = sorted((repo / "dist").glob("pa_automation-*.whl"))[-1]
subprocess.run([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)], check=True)
config = destination / "config"
config.mkdir(mode=0o700)
for source in source_config.glob("*.yaml"):
    shutil.copy2(source, config / source.name)
    (config / source.name).chmod(0o600)
main = yaml.safe_load((config / "pa.yaml").read_text())
main["data_dir"] = "../data"
(config / "pa.yaml").write_text(yaml.safe_dump(main, allow_unicode=True))
data = destination / "data"
data.mkdir(mode=0o700)
with (
    sqlite3.connect(source_data / "pa.sqlite3") as src,
    sqlite3.connect(data / "pa.sqlite3") as dst,
):
    src.backup(dst)
(data / "pa.sqlite3").chmod(0o600)
wrapper = home / ".local/bin/pa"
if wrapper.exists():
    raise SystemExit("Existing ~/.local/bin/pa was preserved; inspect before changing it")
wrapper.parent.mkdir(parents=True, exist_ok=True)
wrapper.write_text(
    "#!/bin/sh\nexec "
    + shlex.quote(str(python))
    + " -m pa --config-dir "
    + shlex.quote(str(config))
    + ' "$@"\n'
)
wrapper.chmod(0o700)
print(f"Installed runtime at {destination}; wrapper: {wrapper}")
