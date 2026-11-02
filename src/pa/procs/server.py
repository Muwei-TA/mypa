import sys
from pathlib import Path

import uvicorn

from pa.api.app import create_app
from pa.store.facades import OwnerFacade


def serve_http(
    config_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    api_token: str | None = None,
) -> None:
    facade = OwnerFacade(config_dir)
    app = create_app(facade, api_token=api_token)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        facade.close()


if __name__ == "__main__":
    serve_http(Path(sys.argv[1]))
