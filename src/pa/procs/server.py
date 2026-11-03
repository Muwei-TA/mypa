import hashlib
import sys
from pathlib import Path

import uvicorn

from pa.api.app import create_app
from pa.secrets.resolver import resolve
from pa.store.facades import OwnerFacade


def serve_http(
    config_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    api_token: str | None = None,
) -> None:
    facade = OwnerFacade(config_dir)
    try:
        token = api_token or resolve(facade.config.main.server.api_owner_token)
        login_service = None
        console = facade.config.main.console
        if console.enabled:
            from pa.common import PAError
            from pa.console.auth import LoginService

            if console.auth is None:
                raise PAError("enabled console requires password_hash and TOTP secret references")
            login_service = LoginService(
                password_hash=resolve(console.auth.password_hash),
                totp_secret=resolve(console.auth.totp),
                server_secret=hashlib.sha256(("pa-console:" + token).encode()).hexdigest(),
            )
        app = create_app(facade, api_token=token, login_service=login_service)
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        facade.close()


if __name__ == "__main__":
    serve_http(Path(sys.argv[1]))
