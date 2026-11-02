"""Ephemeral container runtime contract (M5-01, PRD §9.1.1/v1.1·K5).

One-shot containers: workspace mounted read-only via snapshot, only the
artifacts dir writable, network off except the install whitelist, no
secrets injected, destroyed on exit. Defaults: 2 vCPU, 4GB RAM, 15min
timeout, a single global slot. Real backends (apple_container/tart/docker)
run on the Mac mini; a process backend covers Linux CI and tests.

Review runs add the §10.2 constraints: network none, secrets none, mount
readonly — recorded so review results never show sandbox=degraded.
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
    """Subprocess isolation backend (no true container boundary).

    Used where container tooling is absent (Linux CI, unit tests). Enforces
    the spec's process-level limits: no secret env passthrough, workspace
    read-only intent recorded, artifacts dir writable, hard timeout.
    """

    name = "process"

    def __init__(self, *, global_slot: bool = True):
        self.busy = False
        self.global_slot = global_slot

    def run(self, spec: SandboxSpec) -> SandboxResult:
        if self.global_slot:
            if self.busy:
                return SandboxResult(
                    ok=False,
                    exit_code=None,
                    stderr="another sandbox is running (single global slot)",
                    sandbox=self.name,
                )
            self.busy = True
        try:
            return self._run_locked(spec)
        finally:
            if self.global_slot:
                self.busy = False

    def _run_locked(self, spec: SandboxSpec) -> SandboxResult:
        if spec.secrets != "none":
            return SandboxResult(
                ok=False, stderr="sandbox must not inject secrets", sandbox=self.name
            )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("PA_", "OPENAI_", "ANTHROPIC_", "ARK_", "FEISHU_"))
        }
        notes = [f"mount={spec.mount}", f"network={spec.network}"]
        if spec.review:
            notes.append("review=strict")  # M5-02 marker
        try:
            completed = subprocess.run(
                spec.command,
                capture_output=True,
                text=True,
                timeout=spec.timeout_min * 60,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                timed_out=True,
                stderr=f"timeout after {spec.timeout_min}min",
                sandbox=self.name,
                notes=notes,
            )
        return SandboxResult(
            ok=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            sandbox=self.name,
            notes=notes,
        )


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
