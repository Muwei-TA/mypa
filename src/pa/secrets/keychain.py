import sys

from pa.common import PAError


class KeychainProvider:
    def backend(self):
        if sys.platform != "darwin":
            raise PAError("keychain backend requires macOS")
        # Select macOS explicitly. Never fall back to a plaintext keyring backend.
        from keyring.backends.macOS import Keyring

        return Keyring()

    def get(self, path: str) -> str:
        value = self.backend().get_password("pa", path)
        if not value:
            raise PAError("keychain secret is unavailable", "not_found", 4)
        return value

    def set(self, path: str, value: str) -> None:
        self.backend().set_password("pa", path, value)
