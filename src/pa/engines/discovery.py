import shutil
import tomllib
from pathlib import Path


def discover_local() -> dict:
    engines = {}
    codex = shutil.which("codex")
    if codex:
        config = {"adapter": "codex_cli", "command": [codex]}
        path = Path.home() / ".codex/config.toml"
        if path.exists():
            data = tomllib.loads(path.read_text())
            if isinstance(data.get("model"), str):
                config["model"] = data["model"]
        engines["codex_cli"] = config
    hermes = shutil.which("hermes")
    if hermes:
        engines["hermes_cli"] = {"adapter": "hermes_cli", "command": [hermes]}
    workbuddy = Path(
        "/Applications/WorkBuddy AI.app/Contents/Resources/app.asar.unpacked/"
        "cli/dist/codebuddy-lite-wb.mjs"
    )
    node = shutil.which("node")
    if node and workbuddy.is_file():
        engines["workbuddy_cli"] = {"adapter": "workbuddy_cli", "command": [node, str(workbuddy)]}
    doubao = Path("/Applications/DoubaoWork.app")
    if doubao.exists():
        engines["doubao_desktop"] = {
            "adapter": "gui",
            "enabled": False,
            "app_path": str(doubao),
        }
    return {"engines": engines, "roles": {"dev": [k for k in engines if k != "doubao_desktop"]}}
