"""Sandbox plugin backends (M5-01).

Real container runtimes (apple_container, tart, docker) live on the Mac
mini and register here; the process backend is always available so `pa
sandbox run` works everywhere. Selection order: configured backend first,
process fallback when the backend is missing.
"""

from pa.sandbox.runtime import ProcessSandbox, SandboxBackend


class DockerSandbox(SandboxBackend):
    """Docker backend: one-shot container, destroyed on exit.

    The docker binary is required; when absent, callers fall back to the
    process backend instead of failing the task outright.
    """

    name = "docker"

    def __init__(self, docker_bin: str = "docker"):
        self.docker_bin = docker_bin

    def available(self) -> bool:
        import shutil

        return shutil.which(self.docker_bin) is not None

    def run(self, spec):
        raise NotImplementedError("docker backend ships with the container host")


class AppleContainerSandbox(DockerSandbox):
    name = "apple_container"


class TartSandbox(DockerSandbox):
    name = "tart"


def select_backend(configured: str | None = None) -> tuple[SandboxBackend, str]:
    """Pick the runtime: configured name if importable, else process.

    Returns (backend, chosen_name) so the caller can record which sandbox
    actually ran; the process fallback is explicit, never silent.
    """
    registry = {
        "docker": DockerSandbox,
        "apple_container": AppleContainerSandbox,
        "tart": TartSandbox,
    }
    if configured and configured in registry:
        backend = registry[configured]()
        if getattr(backend, "available", lambda: True)():
            return backend, backend.name
    return ProcessSandbox(), "process"
