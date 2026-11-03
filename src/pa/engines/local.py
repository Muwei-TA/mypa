import json
import shutil
import time
from pathlib import Path

from pa.config.schema import EngineConfig
from pa.engines.base import EngineResult
from pa.engines.process import run_process
from pa.obs.redact import redact
from pa.skills.receipt import Usage, output_schema, parse_receipt


def classify(message: str) -> str:
    lower = message.lower()
    if any(
        s in lower
        for s in ("unauthorized", "not logged", "authentication", "login required", "401")
    ):
        return "auth"
    if any(s in lower for s in ("quota", "rate limit", "429", "credit")):
        return "quota"
    if any(s in lower for s in ("timeout", "connection", "503", "502")):
        return "transient"
    return "fatal"


class LocalCLI:
    adapter = ""

    def __init__(self, engine_id: str, config: EngineConfig):
        self.id, self.config = engine_id, config

    def command(self, request):
        raise NotImplementedError

    def turn_limit(self, request):
        return min(self.config.max_turns, request.max_turns or self.config.max_turns)

    def counters(self, stdout, request):
        return {}

    def reported_turns(self, counters):
        value = counters.get("turns")
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    async def health(self):
        command = self.config.command
        ok = bool(command) and bool(shutil.which(command[0]))
        if ok and len(command) > 1:
            ok = Path(command[1]).is_file()
        return {"installed": ok, "enabled": self.config.enabled, "authentication": "not_tested"}

    async def run(self, request):
        request.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        schema = request.output_dir / "schema.json"
        schema.write_text(json.dumps(output_schema()))
        started = time.monotonic()
        log = request.output_dir / "engine.json"
        usage = Usage()
        turns = None
        try:
            cmd, stdin, env = self.command(request)
            code, stdout, stderr = await run_process(
                cmd,
                cwd=request.workspace or request.output_dir,
                stdin=stdin,
                extra_env=env,
                timeout_s=self.config.timeout_s,
                max_output_bytes=self.config.max_output_bytes,
            )
            usage.duration_s = time.monotonic() - started
            log.write_text(
                json.dumps(redact({"exit_code": code, "stdout": stdout, "stderr": stderr}))
            )
            log.chmod(0o600)
            counters = self.counters(stdout, request)
            usage.input_tokens = int(counters.get("input_tokens", 0))
            usage.output_tokens = int(counters.get("output_tokens", 0))
            turns = self.reported_turns(counters)
            if code or (not stdout.strip() and stderr.strip()):
                message = (stderr or stdout)[-1000:]
                return EngineResult(
                    engine_id=self.id,
                    usage=usage,
                    error=redact(message),
                    error_kind=classify(message),
                    log_path=str(log),
                    turns=turns,
                )
            value, counters = self.extract(stdout, request)
            usage.input_tokens = int(counters.get("input_tokens", 0))
            usage.output_tokens = int(counters.get("output_tokens", 0))
            receipt = parse_receipt(value)
            receipt.usage = usage
            return EngineResult(
                engine_id=self.id, receipt=receipt, usage=usage, log_path=str(log), turns=turns
            )
        except Exception as exc:
            message = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
            usage.duration_s = time.monotonic() - started
            return EngineResult(
                engine_id=self.id,
                usage=usage,
                error=message,
                error_kind="transient" if message == "timeout" else "invalid_output",
                log_path=str(log) if log.exists() else None,
                turns=turns,
            )

    def extract(self, stdout, request):
        return stdout, {}


class CodexCLI(LocalCLI):
    adapter = "codex_cli"

    def command(self, request):
        cmd = self.config.command + [
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-schema",
            str(request.output_dir / "schema.json"),
            "--output-last-message",
            str(request.output_dir / "last-message.json"),
        ]
        if request.workspace:
            cmd += ["--strict-config", *self.code_permissions(request)]
        else:
            cmd += ["--sandbox", "read-only"]
        if self.config.model:
            cmd += ["--model", self.config.model]
        return cmd + ["-"], request.prompt, {}

    @staticmethod
    def code_permissions(request):
        # Official permission profiles keep host reads out of model commands:
        # https://learn.chatgpt.com/docs/permissions#file-access-limited-to-workspace
        # Never combine these with --sandbox, which would select the legacy policy.
        filesystem = {":root": "deny", ":minimal": "read", ":tmpdir": "deny", ":slash_tmp": "deny"}
        if request.git_dir:
            filesystem[str(request.git_dir.resolve())] = "read"
        entries = ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in filesystem.items())
        return [
            "--config",
            'default_permissions="pa_code"',
            "--config",
            'permissions.pa_code.extends=":workspace"',
            "--config",
            "permissions.pa_code.filesystem={" + entries + "}",
            "--config",
            "permissions.pa_code.network.enabled=false",
        ]

    def extract(self, stdout, request):
        final = None
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            detail = item.get("item", {})
            if detail.get("type") == "agent_message":
                final = detail.get("text")
        path = request.output_dir / "last-message.json"
        if path.exists():
            final = path.read_text()
            # No unredacted model output remains outside the protected log.
            path.unlink()
        return final or "", self.counters(stdout, request)

    def counters(self, stdout, request):
        # turn.completed is a user turn, not a count of internal model/tool iterations.
        # Sum usage events but leave model iteration count unknown.
        counters = {"input_tokens": 0, "output_tokens": 0}
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") == "turn.completed":
                for key in counters:
                    counters[key] += int(item.get("usage", {}).get(key, 0))
        return counters


class HermesCLI(LocalCLI):
    adapter = "hermes_cli"

    def command(self, request):
        # Hermes CLI does not accept --max-turns on its current entrypoint.
        # Enforce its iteration cap via HERMES_MAX_ITERATIONS instead.
        cmd = self.config.command + [
            "--ignore-rules",
            "--toolsets",
            "clarify",
            "--reasoning",
            "none",
            "--usage-file",
            str(request.output_dir / "usage.json"),
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        # Hermes -z accepts its prompt as an argument, not stdin; no credentials enter it.
        return (
            cmd + ["-z", request.prompt],
            "",
            {"HERMES_MAX_ITERATIONS": str(self.turn_limit(request))},
        )

    def extract(self, stdout, request):
        return stdout, self.counters(stdout, request)

    def counters(self, stdout, request):
        path = request.output_dir / "usage.json"
        counters = json.loads(path.read_text()) if path.exists() else {}
        if path.exists():
            path.write_text(json.dumps(redact(counters)))
            path.chmod(0o600)
        return {**counters, "turns": counters.get("api_calls")}


class WorkBuddyCLI(LocalCLI):
    adapter = "workbuddy_cli"

    def command(self, request):
        cmd = self.config.command + [
            "--print",
            "--output-format",
            "json",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--no-session-persistence",
            "--max-turns",
            str(self.turn_limit(request)),
            "--permission-mode",
            "dontAsk",
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        # Plain stdin is supported by the installed headless CLI.
        home = str(Path.home() / ".workbuddy-ai")
        return (
            cmd,
            request.prompt,
            {
                "CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS": "1",
                "CODEBUDDY_CONFIG_DIR": home,
                "WORKBUDDY_CONFIG_DIR": home,
            },
        )

    def extract(self, stdout, request):
        data = json.loads(stdout)
        result = data.get("structured_output") or data.get("result") or data.get("response")
        return result, self.counters(stdout, request)

    def counters(self, stdout, request):
        try:
            data = json.loads(stdout)
        except ValueError:
            return {}
        return {**data.get("usage", {}), "turns": data.get("num_turns")}
