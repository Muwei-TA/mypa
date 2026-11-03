"""M5 batch 2: breaker reset / cost summary / budgets / memories API + CLI wiring."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from pa.api.app import create_app

OWNER_TOKEN = "owner-token-1"
AGENT_TOKEN = "agent-token-1"


@pytest.fixture
def client(configured):
    app = create_app(configured, api_token=OWNER_TOKEN, agent_token=AGENT_TOKEN)
    return TestClient(app)


def _owner():
    return {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _agent():
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


# --- engines:reset-breaker ---------------------------------------------------


def test_reset_breaker_endpoint_owner_only(client, configured):
    with configured.engine.begin() as c:
        c.execute(
            text("""
            INSERT INTO engine_health(engine_id,status,last_err,breaker_state,breaker_until)
            VALUES('eng-x','error','auth','open','2099-01-01T00:00:00')
        """)
        )
    denied = client.post("/api/v1/engines/eng-x:reset-breaker", headers=_agent())
    assert denied.status_code == 403
    ok = client.post("/api/v1/engines/eng-x:reset-breaker", headers=_owner())
    assert ok.status_code == 200 and ok.json()["state"] == "closed"
    with configured.engine.connect() as c:
        state = c.execute(
            text("SELECT breaker_state FROM engine_health WHERE engine_id='eng-x'")
        ).scalar_one()
    assert state == "closed"


# --- cost summary ------------------------------------------------------------


def test_cost_summary_groups_by_day(client, configured):
    from pa.cost.budget import record as record_cost

    with configured.engine.begin() as c:
        record_cost(c, task_id="t-1", skill_id="sk-a", engine_id="e-1", cost_cny=2.5)
    response = client.get("/api/v1/cost/summary?group_by=day", headers=_agent())
    assert response.status_code == 200
    items = response.json()["items"]
    assert items and float(items[0]["total_cny"]) >= 2.5

    bad = client.get("/api/v1/cost/summary?group_by=bogus", headers=_agent())
    assert bad.status_code == 422


# --- budgets -----------------------------------------------------------------


def test_budget_put_and_list(client, configured):
    put = client.put(
        "/api/v1/budgets",
        headers=_owner(),
        json={
            "id": "b-api",
            "scope": "skill",
            "scope_id": "sk-a",
            "period": "day",
            "limit_cny": 50,
            "soft_pct": 80,
        },
    )
    assert put.status_code == 200 and put.json()["scope_id"] == "sk-a"
    listing = client.get("/api/v1/budgets", headers=_agent())
    assert any(item["id"] == "b-api" for item in listing.json()["items"])


# --- memories ----------------------------------------------------------------


def test_memory_post_search_delete(client, configured):
    post = client.post(
        "/api/v1/memories",
        headers=_owner(),
        json={"kind": "fact", "title": "发布约定", "content": "周五不做发布"},
    )
    assert post.status_code == 200
    memory_id = post.json()["id"]

    search = client.get("/api/v1/memories?q=发布", headers=_agent())
    assert search.status_code == 200
    assert any(item["id"] == memory_id for item in search.json()["items"])

    listing = client.get("/api/v1/memories", headers=_agent())
    assert any(item["id"] == memory_id for item in listing.json()["items"])

    deleted = client.delete(f"/api/v1/memories/{memory_id}", headers=_owner())
    assert deleted.status_code == 200
    missing = client.delete(f"/api/v1/memories/{memory_id}", headers=_owner())
    assert missing.status_code == 404


# --- CLI wiring --------------------------------------------------------------


def test_cli_m5_commands_registered():
    from typer.testing import CliRunner

    from pa.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "archive",
        "backup",
        "cost",
        "budget",
        "memory",
        "sandbox",
    ):
        assert command in result.output


def test_cli_archive_run_and_status(configured, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pa.cli.main import app

    data_dir = Path(configured.engine.url.database).parent
    monkeypatch.setattr("pa.config.loader.load", lambda *a, **k: configured.config, raising=False)
    runner = CliRunner()
    run = runner.invoke(app, ["--config-dir", str(_config_of(configured)), "archive", "run"])
    assert run.exit_code == 0, run.output
    assert (data_dir / "snapshots").exists()
    status = runner.invoke(app, ["--config-dir", str(_config_of(configured)), "archive", "status"])
    assert status.exit_code == 0
    import json as _json

    assert _json.loads(status.output)["count"] >= 1


def _config_of(configured):
    return configured.config_dir


def test_cli_memory_add_search_delete(configured):
    from typer.testing import CliRunner

    from pa.cli.main import app

    runner = CliRunner()
    added = runner.invoke(
        app,
        [
            "--config-dir",
            str(configured.config_dir),
            "memory",
            "add",
            "CLI记忆",
            "来自CLI的记忆内容",
        ],
    )
    assert added.exit_code == 0, added.output
    import json as _json

    memory_id = _json.loads(added.output)["id"]
    found = runner.invoke(
        app, ["--config-dir", str(configured.config_dir), "memory", "search", "CLI记忆"]
    )
    assert found.exit_code == 0
    assert any(item["id"] == memory_id for item in _json.loads(found.output)["items"])
    deleted = runner.invoke(
        app, ["--config-dir", str(configured.config_dir), "memory", "delete", memory_id]
    )
    assert deleted.exit_code == 0


def test_cli_sandbox_run_processes_command(configured):
    from typer.testing import CliRunner

    from pa.cli.main import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--config-dir", str(configured.config_dir), "sandbox", "run", "echo hi"],
    )
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    import sys

    assert payload["backend"] == ("macos" if sys.platform == "darwin" else "process")
    assert "hi" in payload["stdout"]


def test_cli_secret_rotate_and_list_meta(configured):
    from typer.testing import CliRunner

    from pa.cli.main import app

    runner = CliRunner()
    registered = runner.invoke(
        app,
        [
            "--config-dir",
            str(configured.config_dir),
            "secret",
            "register",
            "secret://keychain/pa/test",
            "--provider",
            "keychain",
            "--rotate-every-days",
            "30",
        ],
    )
    assert registered.exit_code == 0, registered.output
    rotated = runner.invoke(
        app,
        [
            "--config-dir",
            str(configured.config_dir),
            "secret",
            "rotate",
            "secret://keychain/pa/test",
            "--connectivity-ok",
        ],
    )
    assert rotated.exit_code == 0
    import json as _json

    assert _json.loads(rotated.output)["rotated"] is True
    meta = runner.invoke(app, ["--config-dir", str(configured.config_dir), "secret", "list-meta"])
    assert meta.exit_code == 0
    assert any(
        item["ref"] == "secret://keychain/pa/test" for item in _json.loads(meta.output)["items"]
    )


def test_cli_engine_breaker_reset(configured):
    from typer.testing import CliRunner

    from pa.cli.main import app

    with configured.engine.begin() as c:
        c.execute(
            text("""
            INSERT INTO engine_health(engine_id,status,last_err,breaker_state,breaker_until)
            VALUES('eng-cli','error','auth','open','2099-01-01T00:00:00')
        """)
        )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--config-dir", str(configured.config_dir), "engine", "breaker", "eng-cli"],
    )
    assert result.exit_code == 0, result.output
    import json as _json

    assert _json.loads(result.output)["state"] == "closed"
