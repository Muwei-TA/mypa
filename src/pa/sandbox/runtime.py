"""Sandbox contracts and executable backends.

macos enforces kernel filesystem/network policies and uses a disposable HOME.
It is not a VM and does not implement CPU/RAM quotas. process is an unisolated
development runner that refuses strict reviews. Container backends are unavailable.
"""

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CPUS = 2
DEFAULT_MEMORY_GB = 4
DEFAULT_TIMEOUT_MIN = 15


@dataclass
class SandboxSpec:
    command: list[str]
    workspace: Path | None = None
    artifacts_dir: Path | None = None
    cpus: int = DEFAULT_CPUS
    memory_gb: int = DEFAULT_MEMORY_GB
    timeout_min: int = DEFAULT_TIMEOUT_MIN
    network: str = "none"  # none | install_whitelist
    install_allow: list[str] = field(default_factory=list)
    secrets: str = "none"
    mount: str = "readonly"
    review: bool = False  # M5-02: review runs are stricter and logged


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    sandbox: str = "process"  # backend name; never "degraded" for reviews
    notes: list[str] = field(default_factory=list)


class SandboxBackend:
    """Contract for one-shot runtimes; destroy() must run on every exit path."""

    name = ""

    def run(self, spec: SandboxSpec) -> SandboxResult:
        raise NotImplementedError


class ProcessSandbox(SandboxBackend):
    """Unisolated development runner. Strict reviews are refused."""

    name = "process"

    def __init__(self, *, global_slot: bool = True):
        self.busy = False
        self.global_slot = global_slot

    def run(self, spec: SandboxSpec) -> SandboxResult:
        import fcntl
        import tempfile

        if self.busy:
            return SandboxResult(ok=False, stderr="single global slot busy", sandbox=self.name)
        lock_path = Path(tempfile.gettempdir()) / f"pa-sandbox-{os.getuid()}.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if self.global_slot:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return SandboxResult(
                        ok=False, stderr="single global slot busy", sandbox=self.name
                    )
            self.busy = True
            return self._run_locked(spec)
        finally:
            self.busy = False
            os.close(descriptor)

    def _run_locked(self, spec):
        import tempfile

        if spec.review:
            return SandboxResult(
                ok=False,
                sandbox=self.name,
                stderr="isolation_required: process backend cannot run strict reviews",
            )
        if spec.secrets != "none":
            return SandboxResult(
                ok=False, stderr="sandbox must not inject secrets", sandbox=self.name
            )
        with tempfile.TemporaryDirectory(prefix="pa-process-") as home:
            return self._execute(spec, spec.command, Path(home), ["isolation=none"])

    def _execute(self, spec, command, home, notes):
        import signal

        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home),
            "TMPDIR": str(home),
            "LANG": "en_US.UTF-8",
        }
        proc = subprocess.Popen(
            command,
            cwd=spec.workspace or home,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=spec.timeout_min * 60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return SandboxResult(
                ok=False, timed_out=True, sandbox=self.name, stderr="sandbox timeout", notes=notes
            )
        return SandboxResult(
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            sandbox=self.name,
            notes=notes,
        )


class MacOSSandbox(ProcessSandbox):
    """macOS kernel filesystem/network policy; not a VM or a container.

    CPU/RAM quotas and install-domain networking are deliberately unsupported.
    Only system libraries, Python runtime, the workspace and artifacts can be read.
    Strict runs cannot read the host home, write the workspace or open sockets.
    """

    name = "macos"

    def _run_locked(self, spec):
        import json
        import sys
        import tempfile

        if spec.secrets != "none" or spec.network != "none" or spec.mount != "readonly":
            return SandboxResult(
                ok=False,
                sandbox=self.name,
                stderr="unsupported sandbox policy; network none/readonly/no secrets required",
            )
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            return SandboxResult(ok=False, sandbox=self.name, stderr="macOS sandbox unavailable")
        with tempfile.TemporaryDirectory(prefix="pa-macos-sandbox-") as temporary:
            home = Path(temporary).resolve()
            reads = [
                "/System",
                "/usr",
                "/bin",
                "/sbin",
                "/Library/Apple",
                str(Path(sys.base_prefix).resolve()),
                str(Path(sys.prefix).resolve()),
                str(home),
            ]
            reads += [str(p.resolve()) for p in (spec.workspace, spec.artifacts_dir) if p]
            writes = [str(home)]
            if spec.artifacts_dir:
                spec.artifacts_dir.mkdir(parents=True, exist_ok=True)
                writes.append(str(spec.artifacts_dir.resolve()))
            quote = json.dumps
            profile = "(version 1)(deny default)(allow process-fork)(allow process-exec)"
            profile += "(allow sysctl-read)(allow file-read-metadata)"
            profile += '(allow file-read* (literal "/") (literal "/dev/urandom"))'
            profile += "(allow file-read* " + " ".join(f"(subpath {quote(p)})" for p in reads) + ")"
            profile += (
                "(allow file-write* " + " ".join(f"(subpath {quote(p)})" for p in writes) + ")"
            )
            profile += '(allow file-read* file-write* (literal "/dev/null"))'
            command = ["/usr/bin/sandbox-exec", "-p", profile, *spec.command]
            notes = ["mount=readonly", "network=none", "secrets=none", "cpu_ram_quotas=unsupported"]
            if spec.review:
                notes.append("review=strict")
            return self._execute(spec, command, home, notes)


_SLOTS = {"busy": False}


def global_slot_free() -> bool:
    """M5-01: only one sandbox may run at a time, process-wide."""
    return not _SLOTS["busy"]


def acquire_slot() -> bool:
    if _SLOTS["busy"]:
        return False
    _SLOTS["busy"] = True
    return True


def release_slot() -> None:
    _SLOTS["busy"] = False


def review_spec(command: list[str], workspace: Path | None = None) -> SandboxSpec:
    """M5-02: reviews always run with §10.2 strict constraints."""
    return SandboxSpec(
        command=command,
        workspace=workspace,
        network="none",
        secrets="none",
        mount="readonly",
        review=True,
    )


def shell_command(text: str) -> list[str]:
    """Parse a shell string for sandbox run CLI passthrough."""
    return shlex.split(text)
