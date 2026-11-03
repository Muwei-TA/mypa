"""Regression coverage for the 2026-11-03 review findings."""

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from pa.api.app import create_app
from pa.config.loader import load
from pa.config.setup import save_files
from pa.engines.base import EngineResult
from pa.ingress.manual import ManualAdapter
from pa.procs.worker import Worker
from pa.skills.loader import load_skill
from pa.skills.receipt import Receipt, Usage


def emit(facade, skill="sample-direct"):
    return facade.emit(
        ManualAdapter().parse(
            {"type": "manual.test", "payload": {"skill": skill, "text": "review input"}}
        )
    )["tasks"][0]


class FakeEngine:
    calls = 0

    async def health(self):
        return {"installed": True}

    async def run(self, request):
        self.calls += 1
        return EngineResult(
            engine_id="fake",
            turns=1,
            receipt=Receipt(
                status="succeeded",
                summary="review fixture",
                code_changed=False,
                confidence=1,
                risk="low",
                evidence=[],
                usage=Usage(),
            ),
        )


def test_config_save_two_files(configured):
    client = TestClient(create_app(configured, api_token="owner"), raise_server_exceptions=False)
    headers = {"Authorization": "Bearer owner"}
    first = client.put(
        "/api/v1/config/notify.yaml",
        headers=headers,
        json={"value": {"target": {"type": "chat", "id": "review"}}, "base_revision": 0},
    )
    assert first.status_code == 200
    second = client.put(
        "/api/v1/config/principals.yaml",
        headers=headers,
        json={"value": {"owner": {"github": "review-owner"}}, "base_revision": 0},
    )
    assert second.status_code == 200, (
        second.status_code,
        second.text,
        load(configured.config_dir).principals.owner.github,
    )


def test_console_login_then_cancel(configured):
    from pa.console.auth import LoginService, hash_password, totp_now

    task = emit(configured)
    service = LoginService(
        password_hash=hash_password("review-password"),
        totp_secret="JBSWY3DPEHPK3PXP",
        server_secret="review-only",
    )
    client = TestClient(create_app(configured, api_token="owner", login_service=service))
    response = client.post(
        "/console/login",
        json={"password": "review-password", "totp": totp_now(service.totp_secret)},
    )
    assert response.status_code == 200
    assert client.get("/console/").status_code == 200
    cancelled = client.post(
        f"/api/v1/tasks/{task}:cancel", headers={"X-CSRF-Token": response.json()["csrf"]}
    )
    assert cancelled.status_code == 200, cancelled.json()


def test_server_mounts_configured_console(configured, monkeypatch):
    from pa.console.auth import hash_password
    from pa.procs.server import serve_http

    monkeypatch.setenv("REVIEW_PASSWORD_HASH", hash_password("review-password"))
    monkeypatch.setenv("REVIEW_TOTP", "JBSWY3DPEHPK3PXP")

    config = load(configured.config_dir).main.model_dump(mode="json")
    config["console"] = {
        "enabled": True,
        "auth": {
            "password_hash": "secret://env/REVIEW_PASSWORD_HASH",
            "totp": "secret://env/REVIEW_TOTP",
        },
    }
    save_files(configured.config_dir, {"pa.yaml": config})
    apps = []
    monkeypatch.setattr("pa.procs.server.uvicorn.run", lambda app, **kwargs: apps.append(app))
    serve_http(configured.config_dir, api_token="owner")
    response = TestClient(apps[0]).get("/console/login-page")
    assert response.status_code == 200, (response.status_code, response.text)


def test_mcp_missing_judge_denies_gated_tool():
    from pa.tools.mcp_server import McpServer

    calls = []
    server = McpServer(executor=lambda tool, args: calls.append(tool))
    token = server.open_session("review-task", load_skill("sample-direct"))
    response = server.handle(
        json.dumps(
            {"token": token, "method": "tools/call", "tool": "feishu_notify", "arguments": {}}
        ).encode()
    )
    assert not calls, (calls, response)


def test_review_sandbox_cannot_write_workspace(tmp_path):
    from pa.sandbox.runtime import ProcessSandbox, review_spec

    target = tmp_path / "protected.txt"
    target.write_text("original")
    result = ProcessSandbox().run(
        review_spec(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('changed')",
                str(target),
            ],
            workspace=tmp_path,
        )
    )
    assert target.read_text() == "original", result


def test_review_sandbox_does_not_inherit_credentials(monkeypatch):
    from pa.sandbox.runtime import ProcessSandbox, review_spec

    monkeypatch.setenv("GITHUB_TOKEN", "review-sentinel-not-a-secret")
    result = ProcessSandbox().run(
        review_spec([sys.executable, "-c", "import os; print(bool(os.getenv('GITHUB_TOKEN')))"])
    )
    assert not result.ok and "isolation_required" in result.stderr
    assert not result.stdout


def test_adapter_check_summary_accepted_by_merge_gate():
    from pa.ci.merge_gate import merge_gate
    from pa.config.schema import MergePolicy
    from pa.scm.base import CheckSummary

    policy = MergePolicy.model_validate(
        {"matrix": {"personal": {"low": "auto_release", "medium": "confirm", "high": "confirm"}}}
    )
    verdict = merge_gate(
        policy, repo="owner/repo", risk="low", checks=CheckSummary(state="success")
    )
    assert verdict["action"] == "auto_release"


def test_github_checks_include_failed_ai_review():
    from pa.scm.base import PR
    from pa.scm.github import GitHubAdapter

    class Client:
        async def request(self, method, path, **kwargs):
            if path.endswith("/check-runs"):
                return {
                    "check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]
                }
            return {
                "state": "failure",
                "statuses": [{"context": "pa/ai-review", "state": "failure"}],
            }

    checks = asyncio.run(
        GitHubAdapter(client=Client()).get_checks(
            "owner/repo", PR(1, "feature", "main", "review", "", "open", sha="abc")
        )
    )
    assert checks.state != "success", checks


def test_sample_code_reaches_worker(configured, tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    (repo / "hello.txt").write_text("before")
    git("add", "--all")
    git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "initial")
    rules = load(configured.config_dir).rules.model_dump(mode="json", by_alias=True)
    rules["rules"].append(
        {
            "id": "review-code",
            "rank": 3000,
            "match": {"source": "manual", "skill": "sample-code"},
            "skill": "sample-code",
            "repo": "review/repo",
        }
    )
    save_files(
        configured.config_dir,
        {"rules.yaml": rules, "code.yaml": {"repos": {"review/repo": {"remote": str(repo)}}}},
    )
    configured.config = load(configured.config_dir)
    task = emit(configured, "sample-code")

    class CodeEngine(FakeEngine):
        async def run(self, request):
            assert request.workspace is not None
            (request.workspace / "hello.txt").write_text("after")
            return await super().run(request)

    fake = CodeEngine()
    outcome = asyncio.run(Worker(configured, configured.config_dir, engines={"fake": fake}).once())
    assert fake.calls == 1, (outcome, configured.show("tasks", task)["status"])
    assert outcome["status"] == "awaiting_decision"  # local patch is not a delivered platform PR
    assert (repo / "hello.txt").read_text() == "before"
    patch = (
        load(configured.config_dir).main.data_path(configured.config_dir)
        / "code-artifacts"
        / task
        / "changes.patch"
    )
    assert "+after" in patch.read_text()


def test_exhausted_engine_budget_prevents_call(configured):
    from pa.cost.budget import check_budgets

    rules = load(configured.config_dir).rules.model_dump(mode="json", by_alias=True)
    rules["rules"].append(
        {
            "id": "review-budget",
            "rank": 3000,
            "match": {"source": "cron", "type": "cron.budget"},
            "skill": "sample-direct",
            "accept_mode": "auto",
        }
    )
    save_files(configured.config_dir, {"rules.yaml": rules})
    configured.config = load(configured.config_dir)
    event = (
        ManualAdapter()
        .parse({"type": "cron.budget", "payload": {"text": "budget review"}})
        .model_copy(update={"source": "cron", "addressed": False})
    )
    task = configured.emit(event)["tasks"][0]
    with configured.engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO budgets(id,scope,scope_id,period,limit_cny,soft_pct) "
                "VALUES('review','engine','fake','day',0,80)"
            )
        )
        assert (
            check_budgets(c, skill_id="sample-direct", engine_id="fake", task_id=task)["engine"][
                "status"
            ]
            == "blocked"
        )
    fake = FakeEngine()
    asyncio.run(Worker(configured, configured.config_dir, engines={"fake": fake}).once())
    assert fake.calls == 0, configured.show("tasks", task)["status"]


def test_decision_retry_resumes_task(configured):
    from pa.notify.cards import create_decision

    task = emit(configured)
    with configured.engine.begin() as c:
        c.execute(text("UPDATE tasks SET status='awaiting_decision' WHERE id=:id"), {"id": task})
        decision = create_decision(
            c,
            task_id=task,
            kind="task",
            question="Retry?",
            recommend="retry",
            options=["retry", "take_over"],
            expires_at="2099-01-01T00:00:00+00:00",
        )
    client = TestClient(create_app(configured, api_token="owner"))
    response = client.post(
        f"/api/v1/decisions/{decision}:answer",
        headers={"Authorization": "Bearer owner"},
        json={"option": "retry"},
    )
    assert response.status_code == 200
    asyncio.run(Worker(configured, configured.config_dir, engines={"fake": FakeEngine()}).once())
    with configured.engine.connect() as c:
        events = c.execute(
            text("SELECT count(*) FROM events WHERE type='decision.submitted'")
        ).scalar_one()
    assert configured.show("tasks", task)["status"] != "awaiting_decision" or events > 0


def test_token_refresher_calls_original_once():
    from pa.scm.http import install_token_refresher

    class Client:
        requests = 0

        async def request(self, *args, **kwargs):
            self.requests += 1
            return {}

    refreshes = []

    async def refresh():
        refreshes.append(True)
        if len(refreshes) == 4:
            raise RuntimeError("review recursion guard")

    client = Client()
    install_token_refresher(client, refresh)
    try:
        asyncio.run(client.request("GET", "/test"))
    except RuntimeError:
        pass
    assert client.requests == 1 and len(refreshes) == 1, (client.requests, len(refreshes))


def test_expired_precedent_not_applied(configured):
    from pa.judge.precedents import add_precedent, check_precedent

    with configured.engine.begin() as c:
        add_precedent(
            c,
            kind="precedent_decided",
            scope="prod",
            title="release",
            content="release",
            expires_at="2000-01-01T00:00:00+00:00",
        )
        verdict = check_precedent(c, question="release", scope="prod")
    assert verdict["status"] == "miss", verdict


def test_breaker_first_transient_failure_stays_closed(configured):
    from pa.obs.breaker import record_failure

    with configured.engine.begin() as c:
        verdict = record_failure(c, "fake", kind="transient")
    assert verdict["state"] != "open", verdict


@pytest.mark.parametrize(
    "method,path,body",
    [
        (
            "PUT",
            "/api/v1/budgets",
            {"id": "review", "scope": "task", "scope_id": "review-task", "limit_cny": 1},
        ),
        ("POST", "/api/v1/memories", {"kind": "profile", "title": "review", "content": "test"}),
    ],
)
def test_new_api_enum_supported_by_database(configured, method, path, body):
    client = TestClient(create_app(configured, api_token="owner"), raise_server_exceptions=False)
    response = client.request(method, path, headers={"Authorization": "Bearer owner"}, json=body)
    assert response.status_code == 200, (response.status_code, response.text)


def test_gui_without_real_driver_unavailable():
    from pa.config.schema import EngineConfig
    from pa.engines.gui.engine import GuiEngine

    engine = GuiEngine("review-gui", EngineConfig(adapter="gui", app_path="/missing/review.app"))
    assert not asyncio.run(engine.health())["installed"]


def test_gui_rejects_real_skill_repo_write():
    from pa.common import PAError
    from pa.engines.gui.limits import check_c_tier_limits

    skill = load_skill("sample-code").model_copy(deep=True)
    skill.risk_ceiling = "low"
    assert skill.permissions.repo_write
    with pytest.raises(PAError):
        check_c_tier_limits(skill=skill)


def test_memory_search_does_not_match_unrelated_rows(configured):
    from pa.judge.precedents import add_precedent
    from pa.memory.store import retrieve

    with configured.engine.begin() as c:
        add_precedent(
            c, kind="precedent_decided", scope="global", title="deployment", content="deployment"
        )
        add_precedent(
            c, kind="precedent_rejected", scope="global", title="unrelated", content="unrelated"
        )
        results = retrieve(c, "deployment")
    assert [r["title"] for r in results] == ["deployment"], [r["title"] for r in results]


def test_repo_write_lock_really_acquires():
    from pa.workspace.mirror import RepoWriteLock

    async def run():
        lock = RepoWriteLock()
        value = await lock.acquire("owner/repo")
        # Close the returned coroutine to avoid leaving a warning from the reproduction.
        if asyncio.iscoroutine(value):
            value.close()
        assert lock._get("owner/repo").locked()

    asyncio.run(run())


def test_disabling_rules_takes_effect_for_next_api_event(configured):
    client = TestClient(create_app(configured, api_token="owner"))
    headers = {"Authorization": "Bearer owner"}
    rules = load(configured.config_dir).rules.model_dump(mode="json", by_alias=True)
    for rule in rules["rules"]:
        rule["enabled"] = False
    saved = client.put(
        "/api/v1/config/rules.yaml",
        headers=headers,
        json={"value": rules, "base_revision": 0},
    )
    assert saved.status_code == 200
    assert not any(r.enabled for r in load(configured.config_dir).rules.rules)
    response = client.post(
        "/api/v1/events",
        headers=headers,
        json={"type": "manual.test", "payload": {"skill": "sample-direct", "text": "disabled"}},
    )
    assert response.status_code == 200
    assert response.json()["tasks"] == [], response.json()
