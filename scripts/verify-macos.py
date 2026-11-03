"""Real macOS CLI/model checks in disposable PA instances, with no notifications.

Usage: uv run --frozen python scripts/verify-macos.py --output .pa-verification/model
Optionally pass --baseline-python to compare the same input with an older installation.
Requires an already authenticated local Codex CLI. Never changes its credentials.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-python", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    production = Path.home() / "Library/Application Support/PA/config/engines.yaml"
    configured = yaml.safe_load(production.read_text())
    entry = next(v for v in configured["engines"].values() if v["adapter"] == "codex_cli")
    entry = {**entry, "timeout_s": 180, "max_turns": 4}
    inputs = "计算 19 + 23。summary 必须包含 PA-MAC-VERIFY 和计算结果，任务完成时据实返回成功回执。"

    def cli(python, config, *argv, measured=False):
        command = [str(python), "-m", "pa", "--config-dir", str(config), *argv]
        if measured:
            command = ["/usr/bin/time", "-l", "-o", str(config.parent / "time.txt"), *command]
        started = time.monotonic()
        result = subprocess.run(command, text=True, capture_output=True, timeout=240, check=False)
        (config.parent / f"{argv[0]}-stdout.txt").write_text(result.stdout)
        (config.parent / f"{argv[0]}-stderr.txt").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"{argv[0]} failed; inspect {config.parent}")
        try:
            return json.loads(result.stdout), time.monotonic() - started
        except ValueError:
            pass
        for line in reversed(result.stdout.splitlines()):
            try:
                return json.loads(line), time.monotonic() - started
            except ValueError:
                continue
        raise RuntimeError("PA did not emit JSON")

    def setup(label, python):
        config = root / label / "config"
        cli(python, config, "init", "--json")
        (config / "engines.yaml").write_text(
            yaml.safe_dump({"engines": {"real": entry}, "roles": {"dev": ["real"]}})
        )
        return config

    def state(config):
        import sqlite3

        with sqlite3.connect(config.parent / "data/pa.sqlite3") as db:
            db.row_factory = sqlite3.Row
            task = dict(db.execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT 1").fetchone())
            run = dict(db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone())
            usage = dict(
                db.execute(
                    "SELECT sum(input_tokens) input_tokens, sum(output_tokens) output_tokens, "
                    "sum(duration_s) duration_s FROM cost_ledger"
                ).fetchone()
            )
        metrics = (config.parent / "time.txt").read_text()
        match = re.search(r"(\d+)\s+maximum resident set size", metrics)
        return {
            "status": task["status"],
            "receipt": json.loads(run["receipt_json"] or "null"),
            "error": run["error_class"],
            "usage": usage,
            "max_rss_bytes": int(match[1]) if match else None,
            "task_id": task["id"],
        }

    reports = {}
    versions = [("current", Path(sys.executable))]
    if args.baseline_python:
        versions.insert(0, ("baseline", args.baseline_python))
    for label, python in versions:
        config = setup(label, python)
        cli(
            python,
            config,
            "event",
            "emit",
            "manual.test",
            "--payload",
            json.dumps({"skill": "sample-direct", "text": inputs}),
            "--json",
        )
        _, elapsed = cli(python, config, "run", "--once", "--json", measured=True)
        report = state(config)
        report["wall_seconds"] = round(elapsed, 3)
        report["correct"] = bool(
            report["receipt"]
            and "PA-MAC-VERIFY" in report["receipt"]["summary"]
            and "42" in report["receipt"]["summary"]
            and report["status"] == "closed"
        )
        reports[label] = report
        print(json.dumps({label: report}, ensure_ascii=False), flush=True)

    # Real model writes only a new task worktree; the source checkout stays intact.
    config = setup("code", Path(sys.executable))
    repo = root / "source-repo"
    repo.mkdir()

    def git(*argv):
        subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, check=True)

    git("init", "-b", "main")
    (repo / "README.md").write_text("VALUE=41\n")
    git("add", "--all")
    git(
        "-c",
        "user.name=PA Verification",
        "-c",
        "user.email=verify@localhost",
        "commit",
        "-m",
        "initial",
    )
    (config / "code.yaml").write_text(
        yaml.safe_dump({"repos": {"verify/repo": {"remote": str(repo)}}})
    )
    (config / "rules.yaml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "verify-code",
                        "rank": 1000,
                        "match": {"source": "manual", "skill": "sample-code"},
                        "skill": "sample-code",
                        "repo": "verify/repo",
                    }
                ]
            }
        )
    )
    cli(
        sys.executable,
        config,
        "event",
        "emit",
        "manual.test",
        "--payload",
        json.dumps(
            {
                "skill": "sample-code",
                "text": (
                    "只把工作区 README.md 的 VALUE=41 改为 VALUE=42。不要修改其他文件。"
                    "不要提交、推送或访问网络。按实际情况返回回执，平台提交由 PA 负责。"
                ),
            }
        ),
        "--json",
    )
    _, elapsed = cli(sys.executable, config, "run", "--once", "--json", measured=True)
    report = state(config)
    patch = config.parent / "data/code-artifacts" / report["task_id"] / "changes.patch"
    report["wall_seconds"] = round(elapsed, 3)
    report["correct"] = (
        (repo / "README.md").read_text() == "VALUE=41\n"
        and patch.is_file()
        and "+VALUE=42" in patch.read_text()
    )
    report["correct"] = report["correct"] and report["status"] == "awaiting_decision"
    reports["code"] = report
    print(json.dumps({"code": report}, ensure_ascii=False), flush=True)
    (root / "results.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    if not all(r["correct"] for r in reports.values()):
        raise SystemExit("real-model verification failed; inspect results.json")


if __name__ == "__main__":
    main()
