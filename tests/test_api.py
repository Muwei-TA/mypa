from fastapi.testclient import TestClient

from pa.api.app import create_app

BODY = {"type": "manual.test", "payload": {"skill": "sample-direct", "text": "hello"}}


def test_healthz_is_public(facade):
    client = TestClient(create_app(facade))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_db_check(facade):
    client = TestClient(create_app(facade))
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["checks"] == [{"name": "db", "ok": True}]
    assert payload["problems"] == []


def test_post_event_creates_task(facade):
    client = TestClient(create_app(facade))
    response = client.post("/api/v1/events", json=BODY)
    assert response.status_code == 200
    result = response.json()
    assert result["event"]["status"] == "accepted"
    assert len(result["tasks"]) == 1


def test_idempotency_key_dedupicates(facade):
    client = TestClient(create_app(facade))
    headers = {"Idempotency-Key": "idem-123"}
    first = client.post("/api/v1/events", json=BODY, headers=headers)
    second = client.post("/api/v1/events", json=BODY, headers=headers)
    assert first.json()["event"]["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["event"]["status"] == "duplicate"


def test_tasks_filter_by_status_and_skill(facade):
    client = TestClient(create_app(facade))
    client.post("/api/v1/events", json=BODY)
    queued = client.get("/api/v1/tasks?status=queued").json()["items"]
    assert queued and all(row["status"] == "queued" for row in queued)
    direct = client.get("/api/v1/tasks?skill=sample-direct").json()["items"]
    assert all(row["skill_id"] == "sample-direct" for row in direct)
    assert client.get("/api/v1/tasks?status=closed").json()["items"] == []


def test_system_pause_and_resume(facade):
    client = TestClient(create_app(facade))
    paused = client.post("/api/v1/system:pause")
    assert paused.status_code == 200
    assert client.get("/api/v1/system/state").json()["paused"] is True
    resumed = client.post("/api/v1/system:resume")
    assert resumed.status_code == 200
    assert client.get("/api/v1/system/state").json()["paused"] is False


def test_bearer_token_required_when_configured(facade):
    client = TestClient(create_app(facade, api_token="secret-token"))
    assert client.get("/api/v1/tasks").status_code == 401
    assert client.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/v1/tasks", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    # The public healthz endpoint stays reachable without a token.
    assert client.get("/healthz").status_code == 200
