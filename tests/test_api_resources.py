"""M3-02 REST surface: resources, audit_log, agent-token boundaries."""

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


@pytest.fixture
def agent_client(configured):
    app = create_app(configured, api_token=OWNER_TOKEN, agent_token=AGENT_TOKEN)
    return TestClient(app)


def _owner(client):
    return {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _agent(client):
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


def _task(configured, dedup):
    from pa.ingress.manual import ManualAdapter

    return configured.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "x"},
                "dedup_key": dedup,
            }
        )
    )["tasks"][0]


# --- reads -------------------------------------------------------------------


def test_task_detail_includes_runs_decisions_prs(client, configured):
    task = _task(configured, "api-1")
    response = client.get(f"/api/v1/tasks/{task}", headers=_owner(client))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task
    assert "timeline" in body and "runs" in body and "decisions" in body
    assert "pull_requests" in body


def test_queue_and_decisions_listing(client, configured):
    _task(configured, "api-2")
    queue = client.get("/api/v1/queue", headers=_owner(client))
    assert queue.status_code == 200 and queue.json()["items"]
    decisions = client.get("/api/v1/decisions?status=pending", headers=_owner(client))
    assert decisions.status_code == 200
    assert decisions.json()["items"] == []


def test_effects_and_observations_listing(client, configured):
    effects = client.get("/api/v1/effects", headers=_owner(client))
    assert effects.status_code == 200
    observations = client.get("/api/v1/observations", headers=_owner(client))
    assert observations.status_code == 200


# --- writes with audit -------------------------------------------------------


def test_task_priority_patch_writes_audit(client, configured):
    task = _task(configured, "api-3")
    response = client.patch(
        f"/api/v1/tasks/{task}", headers=_owner(client), json={"priority": "P1"}
    )
    assert response.status_code == 200
    with configured.engine.connect() as c:
        audit = c.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE action='task.priority'")
        ).scalar_one()
        priority = c.execute(
            text("SELECT priority FROM tasks WHERE id=:id"), {"id": task}
        ).scalar_one()
    assert audit >= 1 and priority == "P1"


def test_decision_answer_owner_only_with_audit(client, configured):
    task = _task(configured, "api-4")
    with configured.engine.begin() as c:
        c.execute(
            text("""
            INSERT INTO decisions(id,task_id,kind,question,nonce,status)
            VALUES('dec-1',:task,'task','继续吗？','n1','pending')
        """),
            {"task": task},
        )
    answer = client.post(
        "/api/v1/decisions/dec-1:answer", headers=_owner(client), json={"option": "approve"}
    )
    assert answer.status_code == 200 and answer.json()["status"] == "answered"
    with configured.engine.connect() as c:
        audit = c.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'decision.answer%'")
        ).scalar_one()
    assert audit >= 1
    # answering twice: 409
    again = client.post(
        "/api/v1/decisions/dec-1:answer", headers=_owner(client), json={"option": "approve"}
    )
    assert again.status_code == 409


def test_cancel_writes_audit(client, configured):
    task = _task(configured, "api-5")
    response = client.post(f"/api/v1/tasks/{task}:cancel", headers=_owner(client))
    assert response.status_code == 200
    with configured.engine.connect() as c:
        audit = c.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE action='task.cancel'")
        ).scalar_one()
    assert audit >= 1


# --- agent token boundaries (D21) ---------------------------------------------


def test_agent_can_read_and_patch(client):
    assert client.get("/api/v1/queue", headers=_agent(client)).status_code == 200
    assert client.get("/api/v1/tasks", headers=_agent(client)).status_code == 200
    assert client.get("/api/v1/decisions", headers=_agent(client)).status_code == 200


def test_agent_cannot_cancel_or_answer(client, configured):
    task = _task(configured, "api-6")
    cancelled = client.post(f"/api/v1/tasks/{task}:cancel", headers=_agent(client))
    assert cancelled.status_code == 403
    answered = client.post(
        "/api/v1/decisions/x:answer", headers=_agent(client), json={"option": "approve"}
    )
    assert answered.status_code == 403


def test_agent_config_readonly(client, configured):
    read = client.get("/api/v1/config/notify.yaml", headers=_agent(client))
    assert read.status_code == 200
    write = client.put(
        "/api/v1/config/notify.yaml",
        headers=_agent(client),
        json={"value": {"target": {"type": "chat", "id": "oc_hack"}}, "base_revision": 0},
    )
    assert write.status_code == 403


def test_owner_config_write_and_conflict(client, configured):
    write = client.put(
        "/api/v1/config/notify.yaml",
        headers=_owner(client),
        json={"value": {"target": {"type": "chat", "id": "oc_new"}}, "base_revision": 0},
    )
    assert write.status_code == 200 and write.json()["revision"] == 1
    # stale base -> 409
    stale = client.put(
        "/api/v1/config/notify.yaml",
        headers=_owner(client),
        json={"value": {"target": {"type": "chat", "id": "oc_2"}}, "base_revision": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "config_conflict"
    versions = client.get("/api/v1/config/notify.yaml/versions", headers=_owner(client))
    assert versions.status_code == 200
    assert versions.json()["items"][0]["revision"] == 1


def test_precedent_add_and_list(client, configured):
    add = client.post(
        "/api/v1/precedents",
        headers=_owner(client),
        json={
            "kind": "precedent_decided",
            "title": "允许夜间清理",
            "content": "夜里可以跑清理任务",
        },
    )
    assert add.status_code == 200
    listing = client.get("/api/v1/precedents", headers=_owner(client))
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(item["title"] == "允许夜间清理" for item in items)
    agent_add = client.post(
        "/api/v1/precedents",
        headers=_agent(client),
        json={"kind": "precedent_decided", "title": "x", "content": "y"},
    )
    assert agent_add.status_code == 403


def test_unknown_token_rejected(client):
    response = client.get("/api/v1/tasks", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
