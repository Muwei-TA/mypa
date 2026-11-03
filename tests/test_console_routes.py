"""Console session route tests (M3-04): login, session, CSRF, overview guard."""

import pytest
from fastapi.testclient import TestClient

from pa.api.app import create_app
from pa.console.auth import SESSION_COOKIE, LoginService, hash_password


class _StubFacade:
    """Minimal facade with an in-memory engine-less overview stub."""

    def __init__(self):
        self.paused = False

    class _Engine:
        def connect(self):
            import contextlib

            return contextlib.nullcontext(self)

        def execute(self, *_args, **_kwargs):
            return []

        def begin(self):
            import contextlib

            return contextlib.nullcontext(self)

    engine = _Engine()

    def list_tasks(self, **_kwargs):
        return {"items": []}


@pytest.fixture()
def login_service():
    return LoginService(
        password_hash=hash_password("s3cret-pa"),
        totp_secret="JBSWY3DPEHPK3PXP",
        server_secret="server-secret-for-tests",
    )


@pytest.fixture()
def client(login_service):
    app = create_app(
        _StubFacade(),
        api_token="owner-token",
        login_service=login_service,
    )
    return TestClient(app)


def _login(client, *, password="s3cret-pa", totp=None, at=None):
    from pa.console.auth import totp_now

    code = totp if totp is not None else totp_now("JBSWY3DPEHPK3PXP")
    return client.post("/console/login", json={"password": password, "totp": code})


def test_login_success_sets_signed_cookie(client):
    response = _login(client)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["csrf"]
    set_cookie = response.headers["set-cookie"]
    assert "pa_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie.replace("samesite", "SameSite")


def test_login_rejects_wrong_password(client):
    response = _login(client, password="wrong")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers or "pa_session" not in response.headers.get(
        "set-cookie", ""
    )


def test_login_rejects_wrong_totp(client):
    response = _login(client, totp="000000")
    assert response.status_code == 401


def test_unauthenticated_overview_rejected(client):
    assert client.get("/console/overview").status_code == 401


def test_session_info_requires_cookie(client):
    assert client.get("/console/session").status_code == 401
    _login(client)
    response = client.get("/console/session")
    assert response.status_code == 200
    assert response.json()["csrf"]


def test_logout_requires_csrf(client):
    _login(client)
    assert client.post("/console/logout").status_code == 403
    csrf = client.get("/console/session").json()["csrf"]
    response = client.post("/console/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert client.get("/console/session").status_code == 401


def test_forged_cookie_rejected(client, login_service):
    _login(client)
    forged = "forged-token.9999999999.deadbeef"
    response = client.get("/console/session", cookies={SESSION_COOKIE: forged})
    assert response.status_code == 401


def test_login_input_validation(client):
    response = client.post("/console/login", json={"password": "", "totp": "12345"})
    assert response.status_code == 422
