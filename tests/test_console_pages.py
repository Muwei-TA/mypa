"""Console page rendering tests (M3-09): guard, templates, static assets."""

import pytest
from fastapi.testclient import TestClient

from pa.api.app import create_app
from pa.console.auth import LoginService, hash_password, totp_now
from pa.store.db import migrate
from pa.store.facades import OwnerFacade


@pytest.fixture()
def facade(config_dir):
    result = OwnerFacade(config_dir)
    migrate(result.engine)
    yield result
    result.close()


@pytest.fixture()
def client(facade):
    service = LoginService(
        password_hash=hash_password("s3cret-pa"),
        totp_secret="JBSWY3DPEHPK3PXP",
        server_secret="server-secret-for-tests",
    )
    app = create_app(facade, api_token="owner-token", login_service=service)
    return TestClient(app)


def _login(client):
    response = client.post(
        "/console/login",
        json={"password": "s3cret-pa", "totp": totp_now("JBSWY3DPEHPK3PXP")},
    )
    assert response.status_code == 200
    return response.json()["csrf"]


def test_pages_require_login(client):
    for path in (
        "/console/",
        "/console/queue-page",
        "/console/decisions-page",
        "/console/engines-page",
        "/console/observations-page",
        "/console/cost-page",
        "/console/config-page",
        "/console/audit-page",
        "/console/search-page",
    ):
        assert client.get(path).status_code in (401, 307), path


def test_login_page_is_public(client):
    response = client.get("/console/login-page")
    assert response.status_code == 200
    assert "登录" in response.text


def test_overview_page_renders_after_login(client):
    _login(client)
    response = client.get("/console/")
    assert response.status_code == 200
    assert "总览" in response.text
    assert "等你处理" in response.text


def test_task_detail_page_renders(client, facade):
    _login(client)
    result = facade.list_tasks(limit=1)
    # any task id works: page reads via show() which 404s on unknown ids
    response = client.get("/console/tasks-page/missing-task")
    assert response.status_code in (200, 404)
    if result["items"]:
        ok = client.get(f"/console/tasks-page/{result['items'][0]['id']}")
        assert ok.status_code == 200
        assert "状态时间线" in ok.text


def test_static_assets_served(client):
    css = client.get("/console/static/console.css")
    assert css.status_code == 200
    assert "topnav" in css.text
    js = client.get("/console/static/console.js")
    assert js.status_code == 200
    assert "X-CSRF-Token" in js.text


def test_queue_and_decisions_pages(client):
    _login(client)
    assert "队列" in client.get("/console/queue-page").text
    assert "决定" in client.get("/console/decisions-page").text


def test_search_page_with_query(client):
    _login(client)
    response = client.get("/console/search-page", params={"q": "#12"})
    assert response.status_code == 200
    assert "搜索" in response.text


def test_config_page_lists_versions(client, config_dir):
    _login(client)
    response = client.get("/console/config-page", params={"file_name": "pa.yaml"})
    assert response.status_code == 200
    assert "pa.yaml" in response.text
    assert "版本历史" in response.text
