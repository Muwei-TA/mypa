"""M3-04 console login (TOTP/CSRF/cookies) and M3-07 overview feed."""

from sqlalchemy import text

from pa.console.auth import (
    LoginService,
    cookie_attributes,
    hash_password,
    totp_now,
    verify_password,
    verify_totp,
)
from pa.console.overview import overview
from pa.store.db import transaction


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


# --- M3-04 login ---------------------------------------------------------------


def test_password_hash_roundtrip():
    stored = hash_password("s3cret-pw")
    assert verify_password("s3cret-pw", stored)
    assert not verify_password("wrong", stored)


def test_totp_generates_and_verifies_with_drift():
    code = totp_now("JBSWY3DPEHPK3PXP", at=1_700_000_000)
    assert len(code) == 6 and code.isdigit()
    assert verify_totp("JBSWY3DPEHPK3PXP", code, at=1_700_000_000)
    assert verify_totp("JBSWY3DPEHPK3PXP", code, at=1_700_000_000 + 29)  # same window
    assert not verify_totp("JBSWY3DPEHPK3PXP", "000000", at=1_700_000_000)
    assert not verify_totp("JBSWY3DPEHPK3PXP", code, at=1_700_000_000 + 120)


def test_login_service_session_and_csrf():
    service = LoginService(
        password_hash=hash_password("pw"),
        totp_secret="JBSWY3DPEHPK3PXP",
        server_secret="srv",
    )
    code = totp_now("JBSWY3DPEHPK3PXP")
    assert service.login(password="pw", totp=code) is not None
    assert service.login(password="bad", totp=code) is None
    assert service.login(password="pw", totp="000001") is None
    session = service.login(password="pw", totp=code)
    signed = service.sign_session(session)
    restored = service.verify_session(signed)
    assert restored is not None and restored.token == session.token
    assert service.check_csrf(restored, restored.csrf)
    assert not service.check_csrf(restored, "forged")
    assert not service.check_csrf(restored, None)


def test_session_expiry_and_bad_signature():
    service = LoginService(
        password_hash=hash_password("pw"),
        totp_secret="JBSWY3DPEHPK3PXP",
        server_secret="srv",
    )
    session = service.login(password="pw", totp=totp_now("JBSWY3DPEHPK3PXP"))
    signed = service.sign_session(session)
    # expired: forge an expiry in the past by signing a stale payload
    import hashlib as _hl
    import hmac as _hmac

    stale_payload = f"{session.token}.1000"
    stale_sig = _hmac.new(b"srv", stale_payload.encode(), _hl.sha256).hexdigest()
    assert service.verify_session(f"{stale_payload}.{stale_sig}") is None
    assert service.verify_session("garbage") is None
    assert service.verify_session(None) is None
    assert service.verify_session(signed) is not None


def test_cookie_flags():
    flags = cookie_attributes()
    assert flags["httponly"] is True
    assert flags["samesite"] == "strict"


# --- M3-07 overview -------------------------------------------------------------


def test_overview_shows_only_owner_waiting(configured):
    answer_task = _task(configured, "ov-1")
    normal_task = _task(configured, "ov-2")
    with transaction(configured.engine) as c:
        c.execute(
            text("""
            INSERT INTO decisions(id,task_id,kind,question,nonce,status)
            VALUES('d-ov1',:task,'task','上线吗？','n','pending')
        """),
            {"task": answer_task},
        )
    with configured.engine.connect() as c:
        result = overview(c)
    stages = {item["stage"] for item in result["items"]}
    assert "owner/answer" in stages
    assert all(stage.startswith("owner/") for stage in stages)
    assert normal_task not in {item["task_id"] for item in result["items"]}
    waiting = next(item for item in result["items"] if item["task_id"] == answer_task)
    assert waiting["decision_id"] == "d-ov1"
    assert waiting["action"] == "answer"
    assert result["counts"]["owner/answer"] == 1
    assert result["total_waiting"] == len(result["items"])


def test_overview_delivery_and_problems(configured):
    task = _task(configured, "ov-3")
    with transaction(configured.engine) as c:
        c.execute(
            text("""
            INSERT INTO effects(id,task_id,kind,idem_key,status,attempts)
            VALUES('ef-ov3',:task,'feishu.send','ov3:1','failed',3)
        """),
            {"task": task},
        )
        c.execute(
            text("""
            INSERT INTO engine_health(engine_id,status,breaker_state)
            VALUES('eng-1','error','open')
        """),
        )
        c.execute(
            text("""
            INSERT INTO system_state(key,value,updated_at)
            VALUES('paused','1','2026-01-01T00:00:00+00:00')
            ON CONFLICT(key) DO UPDATE SET value='1'
        """),
        )
    with configured.engine.connect() as c:
        result = overview(c)
    delivery = next(item for item in result["items"] if item["task_id"] == task)
    assert delivery["stage"] == "owner/delivery"
    assert delivery["effect_id"] == "ef-ov3"
    assert delivery["action"] == "retry_or_drop"
    kinds = {problem["kind"] for problem in result["problems"]}
    assert kinds == {"paused", "engine"}


def test_overview_healthy_system_empty(configured):
    _task(configured, "ov-4")
    with configured.engine.connect() as c:
        result = overview(c)
    assert result["items"] == []
    assert result["problems"] == []
    assert result["total_waiting"] == 0
