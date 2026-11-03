"""Verify the real loopback Uvicorn server with password/TOTP/session/CSRF.

Creates a disposable instance. --keep-server leaves its login page available
for a native-browser visual check; results contain its PID for cleanup.
No credentials are written to the report, command line or config files.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import text

from pa.config.loader import initialize, load
from pa.config.setup import save_files
from pa.console.auth import hash_password, totp_now
from pa.notify.cards import create_decision
from pa.store.db import migrate, transaction
from pa.store.facades import OwnerFacade


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-server", action="store_true")
    parser.add_argument(
        "--ui-demo",
        action="store_true",
        help="Disposable loopback-only demo login: PA-local-verification-only",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, mode=0o700)
    config_dir = root / "config"
    initialize(config_dir)
    main_config = load(config_dir).main.model_dump(mode="json")
    main_config["server"]["api_owner_token"] = "secret://env/PA_VERIFY_OWNER"
    main_config["console"] = {
        "enabled": True,
        "auth": {
            "password_hash": "secret://env/PA_VERIFY_HASH",
            "totp": "secret://env/PA_VERIFY_TOTP",
        },
    }
    save_files(config_dir, {"pa.yaml": main_config})
    facade = OwnerFacade(config_dir)
    migrate(facade.engine)
    password = "PA-local-verification-only" if args.ui_demo else secrets.token_urlsafe(24)
    token = secrets.token_urlsafe(32)
    totp = "JBSWY3DPEHPK3PXP"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "PA_VERIFY_OWNER": token,
        "PA_VERIFY_HASH": hash_password(password),
        "PA_VERIFY_TOTP": totp,
        "PA_VERIFY_CONFIG": str(config_dir),
        "PA_VERIFY_PORT": str(port),
    }
    log = (root / "server.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os; from pathlib import Path; from pa.procs.server import serve_http; "
            "serve_http(Path(os.environ['PA_VERIFY_CONFIG']),port=int(os.environ['PA_VERIFY_PORT']))",
        ],
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    result = {
        "url": f"http://127.0.0.1:{port}/console/login-page",
        "pid": process.pid,
        "checks": {},
    }
    (root / "server.json").write_text(json.dumps(result))
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10
        ) as client:
            for _ in range(100):
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.ConnectError:
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"HTTP server exited; inspect {root / 'server.log'}"
                        ) from None
                time.sleep(0.1)
            checks = result["checks"]
            checks["anonymous_api_denied"] = client.get("/api/v1/tasks").status_code == 401
            checks["login_page"] = client.get("/console/login-page").status_code == 200
            login = client.post(
                "/console/login", json={"password": password, "totp": totp_now(totp)}
            )
            assert login.status_code == 200
            csrf = login.json()["csrf"]
            headers = {"X-CSRF-Token": csrf}
            checks["cookie_flags"] = "HttpOnly" in login.headers.get_list("set-cookie")[0]
            checks["overview_authenticated"] = client.get("/console/").status_code == 200
            created = client.post(
                "/api/v1/events",
                headers=headers,
                json={
                    "type": "manual.test",
                    "payload": {"skill": "sample-direct", "text": "HTTP verification"},
                },
            )
            task = created.json()["tasks"][0]
            checks["missing_csrf_denied"] = (
                client.post(f"/api/v1/tasks/{task}:cancel").status_code == 403
            )
            cancelled = client.post(f"/api/v1/tasks/{task}:cancel", headers=headers)
            checks["cookie_cancel"] = (
                cancelled.status_code == 200
                and client.get(f"/api/v1/tasks/{task}").json()["status"] == "cancelled"
            )
            checks["two_config_files"] = all(
                client.put(
                    f"/api/v1/config/{name}",
                    headers=headers,
                    json={"value": value, "base_revision": 0},
                ).status_code
                == 200
                for name, value in [
                    ("notify.yaml", {"target": {"type": "user", "id": "test-only"}}),
                    ("principals.yaml", {"owner": {"github": "test-only"}}),
                ]
            )
            rules = load(config_dir).rules.model_dump(mode="json", by_alias=True)
            for rule in rules["rules"]:
                rule["enabled"] = False
            saved = client.put(
                "/api/v1/config/rules.yaml",
                headers=headers,
                json={"value": rules, "base_revision": 0},
            )
            after = client.post(
                "/api/v1/events",
                headers=headers,
                json={
                    "type": "manual.test",
                    "payload": {"skill": "sample-direct", "text": "disabled rule"},
                },
            )
            checks["disabled_rules_immediate"] = (
                saved.status_code == 200 and after.json()["tasks"] == []
            )
            for rule in rules["rules"]:
                rule["enabled"] = True
            client.put(
                "/api/v1/config/rules.yaml",
                headers=headers,
                json={"value": rules, "base_revision": 1},
            ).raise_for_status()
            task = client.post(
                "/api/v1/events",
                headers=headers,
                json={
                    "type": "manual.test",
                    "payload": {"skill": "sample-direct", "text": "retry"},
                },
            ).json()["tasks"][0]
            with transaction(facade.engine) as c:
                c.execute(
                    text("UPDATE tasks SET status='awaiting_decision' WHERE id=:id"), {"id": task}
                )
                decision = create_decision(
                    c,
                    task_id=task,
                    kind="task",
                    question="Retry?",
                    recommend="retry",
                    options=["retry", "take_over"],
                    expires_at="2099-01-01T00:00:00+00:00",
                )
            answered = client.post(
                f"/api/v1/decisions/{decision}:answer", headers=headers, json={"option": "retry"}
            )
            checks["decision_retry_requeued"] = (
                answered.status_code == 200
                and client.get(f"/api/v1/tasks/{task}").json()["status"] == "queued"
            )
            client.post("/console/logout", headers=headers).raise_for_status()
            checks["logout"] = client.get("/api/v1/tasks").status_code == 401
        (root / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not all(checks.values()):
            raise SystemExit("real HTTP verification failed")
    finally:
        facade.close()
        log.close()
        if not args.keep_server:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
