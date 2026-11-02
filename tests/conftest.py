import pytest

from pa.config.loader import initialize
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
