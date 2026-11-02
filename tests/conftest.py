import sys

import pytest

from pa.config.loader import initialize, load
from pa.config.setup import configure_feishu, save_files
from pa.store.db import migrate
from pa.store.facades import OwnerFacade


@pytest.fixture
def config_dir(tmp_path):
    path = tmp_path / "config"
    initialize(path)
    return path


@pytest.fixture
def facade(config_dir):
    result = OwnerFacade(config_dir)
    migrate(result.engine)
    yield result
    result.close()


@pytest.fixture
def configured(facade, config_dir):
    save_files(
        config_dir,
        {
            "engines.yaml": {
                "engines": {"fake": {"adapter": "codex_cli", "command": [sys.executable]}},
                "roles": {"dev": ["fake"]},
            }
        },
    )
    configure_feishu(config_dir, app_id="cli_test", owner_open_id="ou_owner")
    facade.config = load(config_dir)
    return facade
