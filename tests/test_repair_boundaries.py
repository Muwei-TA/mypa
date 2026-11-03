"""Exercise transaction failures, real OS isolation and entry-to-outcome boundaries."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from pa.api.app import create_app
from pa.common import PAError
from pa.config.loader import load
from pa.config.setup import save_files
from pa.config.versions import current_revision, save_versioned
from pa.console.auth import LoginService, hash_password, totp_now
from pa.cost.budget import reserve
from pa.engines.base import EngineResult
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.sandbox.runtime import MacOSSandbox, review_spec
from pa.store.db import transaction
from pa.store.facades import OwnerFacade


def test_config_outer_rollback_restores_file_and_revision(configured):
    target = configured.config_dir / "notify.yaml"
    original = target.read_bytes()
    with pytest.raises(RuntimeError), transaction(configured.engine) as c:
        save_versioned(
            c,
            configured.config_dir,
            file_name="notify.yaml",
            value={"target": {"type": "chat", "id": "rolled-back"}},
            base_revision=0,
        )
        raise RuntimeError("simulate later transaction failure")
    assert target.read_bytes() == original
    assert not list(configured.config_dir.glob(".config-txn-*.json"))
    with configured.engine.connect() as c:
        assert current_revision(c, "notify.yaml") == 0


def test_config_crash_recovery_restores_uncommitted_disk_write(configured):
    target = configured.config_dir / "notify.yaml"
    before = target.read_bytes()
    source = """
import os,sys
from pathlib import Path
from pa.store.facades import OwnerFacade
from pa.store.db import transaction
from pa.config.versions import save_versioned
f=OwnerFacade(Path(sys.argv[1]))
with transaction(f.engine) as c:
    save_versioned(c,f.config_dir,file_name='notify.yaml',base_revision=0,
                   value={'target':{'type':'chat','id':'interrupted'}})
    os._exit(17)
"""
    child = subprocess.run(
        [sys.executable, "-c", source, str(configured.config_dir)], capture_output=True, timeout=15
    )
    assert child.returncode == 17, child.stderr
    assert target.read_bytes() != before
    recovered = OwnerFacade(configured.config_dir)
    recovered.close()
    assert target.read_bytes() == before
    assert not list(configured.config_dir.glob(".config-txn-*.json"))


def test_config_concurrent_stale_writer_cannot_overwrite(configured):
    def write(value):
        try:
            with transaction(configured.engine) as c:
                save_versioned(
                    c,
                    configured.config_dir,
                    file_name="notify.yaml",
                    base_revision=0,
                    value={"target": {"type": "chat", "id": value}},
                )
            return "saved"
        except PAError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["first", "second"]))
    assert sorted(results) == ["config_conflict", "saved"]
    with configured.engine.connect() as c:
        assert current_revision(c, "notify.yaml") == 1


def test_console_mutation_requires_bound_csrf_and_does_not_accept_agent(configured):
    service = LoginService(
        password_hash=hash_password("test-password"),
        totp_secret="JBSWY3DPEHPK3PXP",
        server_secret="test-only",
    )
    client = TestClient(
        create_app(configured, api_token="owner", agent_token="agent", login_service=service)
    )
    task = client.post(
        "/api/v1/events",
        headers={"Authorization": "Bearer owner"},
        json={"type": "manual.test", "payload": {"skill": "sample-direct"}},
    ).json()["tasks"][0]
    logged = client.post(
        "/console/login", json={"password": "test-password", "totp": totp_now(service.totp_secret)}
    )
    assert logged.status_code == 200 and client.cookies.get("pa_csrf")
    endpoint = f"/api/v1/tasks/{task}:cancel"
    assert client.post(endpoint).status_code == 403
    assert client.post(endpoint, headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert client.post(endpoint, headers={"Authorization": "Bearer agent"}).status_code == 403
    assert configured.show("tasks", task)["status"] == "queued"
    assert client.post(endpoint, headers={"X-CSRF-Token": logged.json()["csrf"]}).status_code == 200
    assert configured.show("tasks", task)["status"] == "cancelled"


def test_budget_reservation_serializes_two_callers(configured):
    with transaction(configured.engine) as c:
        c.execute(
            text("INSERT INTO budgets(id,scope,period,limit_cny) VALUES('cap','global','day',1)")
        )

    def attempt(index):
        with transaction(configured.engine) as c:
            return reserve(
                c,
                task={"id": str(index), "skill_id": "sample-direct"},
                engine_id="fake",
                run_id=str(index),
                price={"mode": "per_call", "cny": 1},
                tokens=100,
                seconds=10,
            )

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [1, 2]))
    assert sorted(results) == [False, True]
    with configured.engine.connect() as c:
        assert c.execute(text("SELECT sum(cost_cny) FROM budget_reservations")).scalar_one() == 1


def test_budget_stops_receipt_repair_before_second_billed_call(configured):
    config = load(configured.config_dir).engines.model_dump(mode="json")
    config["engines"]["fake"]["pricing"] = {"mode": "per_call", "cny": 1}
    save_files(configured.config_dir, {"engines.yaml": config})
    with transaction(configured.engine) as c:
        c.execute(
            text("INSERT INTO budgets(id,scope,period,limit_cny) VALUES('cap','global','day',1)")
        )
    configured.emit(
        ManualAdapter().parse({"type": "manual.test", "payload": {"skill": "sample-direct"}})
    )

    class Engine:
        calls = 0

        async def health(self):
            return {"installed": True}

        async def run(self, request):
            self.calls += 1
            return EngineResult(
                engine_id="fake", error="bad JSON", error_kind="invalid_output", turns=1
            )

    engine = Engine()
    outcome = asyncio.run(
        Worker(configured, configured.config_dir, engines={"fake": engine}).once()
    )
    assert outcome["status"] == "awaiting_decision" and engine.calls == 1
    with configured.engine.connect() as c:
        assert c.execute(text("SELECT sum(cost_cny) FROM cost_ledger")).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM budget_reservations")).scalar_one() == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real macOS kernel sandbox")
def test_macos_strict_isolation_rejects_host_reads_writes_and_sockets(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    host = tmp_path / "outside-secret"
    host.write_text("test-sentinel")
    protected = workspace / "input.txt"
    protected.write_text("original")
    monkeypatch.setenv("GITHUB_TOKEN", "test-not-a-real-token")
    source = """
import os,socket,sys,pathlib,json
blocked=[]
for name,operation in [
 ('host_read',lambda:pathlib.Path(sys.argv[1]).read_text()),
 ('workspace_write',lambda:pathlib.Path(sys.argv[2]).write_text('changed')),
 ('network',lambda:socket.create_connection(('127.0.0.1',9),1))]:
 try: operation()
 except PermissionError: blocked.append(name)
pathlib.Path(sys.argv[3]).write_text('artifact')
print(json.dumps({'blocked':blocked,'credential_present':bool(os.getenv('GITHUB_TOKEN'))}))
"""
    spec = review_spec(
        [sys.executable, "-c", source, str(host), str(protected), str(artifacts / "result")],
        workspace,
    )
    spec.artifacts_dir = artifacts
    result = MacOSSandbox().run(spec)
    assert result.ok, result.stderr
    payload = json.loads(result.stdout)
    assert sorted(payload["blocked"]) == ["host_read", "network", "workspace_write"]
    assert not payload["credential_present"]
    assert protected.read_text() == "original" and (artifacts / "result").read_text() == "artifact"


def test_decision_reject_and_invalid_option_have_real_outcomes(configured):
    from pa.notify.cards import create_decision

    task = configured.emit(
        ManualAdapter().parse({"type": "manual.test", "payload": {"skill": "sample-direct"}})
    )["tasks"][0]
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE tasks SET status='awaiting_decision' WHERE id=:id"), {"id": task})
        decision = create_decision(
            c,
            task_id=task,
            kind="task",
            question="Continue?",
            recommend="reject",
            options=["reject", "retry"],
            expires_at="2099-01-01T00:00:00+00:00",
        )
    client = TestClient(create_app(configured, api_token="owner"))
    endpoint = f"/api/v1/decisions/{decision}:answer"
    headers = {"Authorization": "Bearer owner"}
    assert client.post(endpoint, headers=headers, json={"option": "approve"}).status_code == 400
    assert client.post(endpoint, headers=headers, json={"option": "reject"}).status_code == 200
    assert configured.show("tasks", task)["status"] == "suspended"
    assert client.post(endpoint, headers=headers, json={"option": "retry"}).status_code == 409


@pytest.mark.skipif(sys.platform != "darwin", reason="real Codex macOS permission profile")
def test_codex_code_profile_restricts_real_commands(tmp_path):
    import shutil

    from pa.engines.base import EngineRequest
    from pa.engines.local import CodexCLI

    codex = shutil.which("codex")
    if not codex:
        pytest.skip("Codex CLI is not installed")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".codex").mkdir()
    outside = tmp_path / "outside"
    outside.write_text("test-only")
    request = EngineRequest(
        task_id="test", prompt="", workspace=workspace, output_dir=tmp_path / "out"
    )
    probe = (
        'printf yes > ok; /bin/cat "$1" >/dev/null; '
        "printf nope > .codex/guard; "
        "/usr/bin/curl -sv --connect-timeout 1 http://127.0.0.1:9; exit 0"
    )
    command = [
        codex,
        "sandbox",
        "-P",
        "pa_code",
        *CodexCLI.code_permissions(request),
        "-C",
        str(workspace),
        "/bin/sh",
        "-c",
        probe,
        "probe",
        str(outside),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert (workspace / "ok").read_text() == "yes"
    assert not (workspace / ".codex/guard").exists()
    assert result.stderr.count("Operation not permitted") >= 3, result.stderr
    assert outside.read_text() == "test-only"


def test_inflight_result_uses_accepted_engine_config(configured):
    from pa.skills.receipt import Receipt, Usage

    task = configured.emit(
        ManualAdapter().parse({"type": "manual.test", "payload": {"skill": "sample-direct"}})
    )["tasks"][0]

    class Engine:
        async def health(self):
            return {"installed": True}

        async def run(self, request):
            save_files(
                configured.config_dir, {"engines.yaml": {"engines": {}, "roles": {"dev": []}}}
            )
            return EngineResult(
                engine_id="fake",
                turns=1,
                receipt=Receipt(
                    status="succeeded",
                    summary="finished",
                    code_changed=False,
                    confidence=1,
                    risk="low",
                    evidence=[],
                    usage=Usage(input_tokens=12),
                ),
            )

    outcome = asyncio.run(
        Worker(configured, configured.config_dir, engines={"fake": Engine()}).once()
    )
    assert outcome["status"] == "closed"
    assert configured.show("tasks", task)["status"] == "closed"
    with configured.engine.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM cost_ledger WHERE engine_id='fake'")).scalar_one()
            == 1
        )
