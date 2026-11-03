"""Select an implemented backend; never downgrade strict isolation to a process."""

from pa.sandbox.runtime import MacOSSandbox, ProcessSandbox, SandboxBackend


class DockerSandbox(SandboxBackend):
    """Reserved interface; not selectable until a real implementation lands."""

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
    """Use macos by default on Mac; process elsewhere. Explicit unknown backends fail."""
    import sys

    from pa.common import PAError

    if configured in ("docker", "apple_container", "tart"):
        raise PAError(f"sandbox backend {configured} is not implemented; refusing process fallback")
    if configured == "macos" or (configured is None and sys.platform == "darwin"):
        return MacOSSandbox(), "macos"
    if configured not in (None, "process"):
        raise PAError("unknown sandbox backend")
    return ProcessSandbox(), "process"
