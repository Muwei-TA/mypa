"""No real tokens are used or emitted by the credential setup tests."""

import runpy
import stat
from pathlib import Path

import pytest


@pytest.fixture
def setup_script():
    return runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/setup-gitea-sandbox-credentials.py")
    )


def test_token_identity_and_repo_acl_are_checked(monkeypatch, setup_script):
    validate = setup_script["validate"]
    SetupError = setup_script["SetupError"]
    fake = "safe-test-token-placeholder-" + "x" * 32

    def mock_get(_token, path):
        if path == "/user":
            return {"login": "pa-agent-e2e", "is_admin": False}
        if path == "/repos/muwei/pa-v11-acceptance-sandbox":
            return {"permissions": {"pull": True, "push": True, "admin": False}}
        return None

    monkeypatch.setitem(validate.__globals__, "_get", mock_get)
    validate(fake, "pa-agent-e2e")
    with pytest.raises(SetupError, match="must authenticate"):
        validate(fake, "pa-merger-e2e")


def test_production_write_access_is_rejected(monkeypatch, setup_script):
    validate = setup_script["validate"]
    SetupError = setup_script["SetupError"]
    fake = "safe-test-token-placeholder-" + "x" * 32

    def mock_get(_token, path):
        if path == "/user":
            return {"login": "pa-merger-e2e", "is_admin": False}
        if path.endswith("acceptance-sandbox"):
            return {"permissions": {"pull": True, "push": True, "admin": False}}
        return {"permissions": {"pull": True, "push": True, "admin": False}}

    monkeypatch.setitem(validate.__globals__, "_get", mock_get)
    with pytest.raises(SetupError, match="production write access"):
        validate(fake, "pa-merger-e2e")


def test_tokens_saved_only_in_private_files(tmp_path, setup_script, monkeypatch):
    store = setup_script["store"]
    check_saved = setup_script["check_saved"]
    SetupError = setup_script["SetupError"]
    token = "safe-test-token-placeholder-" + "x" * 32
    store(token, "agent", tmp_path / "secrets")
    assert stat.S_IMODE((tmp_path / "secrets").stat().st_mode) == 0o700
    f = tmp_path / "secrets" / "agent.token"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert f.read_text().strip() == token
    with pytest.raises(SetupError, match="refusing to overwrite"):
        store(token, "agent", tmp_path / "secrets")
    monkeypatch.setitem(check_saved.__globals__, "validate", lambda value, username: None)
    check_saved("agent", tmp_path / "secrets")
    f.chmod(0o644)
    with pytest.raises(SetupError, match="0600"):
        check_saved("agent", tmp_path / "secrets")
