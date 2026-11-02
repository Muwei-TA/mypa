"""FastAPI application and REST endpoints (M1-18 + M3-02).

All owner endpoints require a configured Bearer token, including on loopback.
Only /healthz (and the configured webhook endpoints) are public. Agent
tokens (`agent_token=`) map to AgentFacade: reads and task submissions only;
config writes and destructive actions are absent from their surface.
"""

import json
import secrets

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import text

from pa.common import PAError, now
from pa.config.schema import Model
from pa.ingress.github import GiteaWebhookAdapter, GitHubWebhookAdapter
from pa.ingress.manual import ManualAdapter
from pa.ingress.webhook_common import WebhookGuard
from pa.store.models import TaskStatus
from pa.store.roles import AgentFacade


class ManualEventInput(Model):
    type: str = Field(min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=80)
    payload: dict = Field(default_factory=dict)
    subject: str | None = None


class TaskPatch(Model):
    priority: str


class DecisionAnswer(Model):
    option: str


class ConfigWrite(Model):
    value: dict
    base_revision: int = Field(ge=0)


_STATUS_CODE = {
    "not_found": 404,
    "forbidden": 403,
    "conflict": 409,
    "bad_request": 400,
}


def create_app(
    facade,
    *,
    api_token: str | None = None,
    agent_token: str | None = None,
    webhook_secrets: dict[str, str] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="PA Automation", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    router = APIRouter(prefix="/api/v1")
    guards = {k: WebhookGuard(secret=v) for k, v in (webhook_secrets or {}).items()}
    agent_facade = AgentFacade(facade) if agent_token else None

    def require_auth(authorization: str | None = Header(default=None)):
        if (
            not api_token
            or not authorization
            or not secrets.compare_digest(authorization.encode(), f"Bearer {api_token}".encode())
        ):
            raise HTTPException(
                status_code=401,
                detail="invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def require_agent(authorization: str | None = Header(default=None)):
        """Agent tokens authenticate as agent-role; owner tokens pass too."""
        if agent_token and authorization and secrets.compare_digest(
            authorization.encode(), f"Bearer {agent_token}".encode()
        ):
            return agent_facade
        require_auth(authorization)
        return facade

    def require_owner(authorization: str | None = Header(default=None)):
        """Owner-only surface: agent tokens are structurally rejected."""
        if agent_token and authorization and secrets.compare_digest(
            authorization.encode(), f"Bearer {agent_token}".encode()
        ):
            raise HTTPException(status_code=403, detail="agent tokens cannot call this")
        require_auth(authorization)
        return facade

    @app.exception_handler(RequestValidationError)
    def invalid_request(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "details": [
                        {"path": ".".join(map(str, error["loc"])), "type": error["type"]}
                        for error in exc.errors()
                    ],
                }
            },
        )

    @app.exception_handler(PAError)
    def handle_pa_error(request, exc):
        return JSONResponse(
            status_code=_STATUS_CODE.get(exc.code, 400),
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    async def _receive_webhook(kind: str, request: Request) -> JSONResponse | dict:
        guard = guards.get(kind)
        if guard is None:
            raise HTTPException(status_code=404, detail="webhook not configured")
        body = await request.body()
        headers = {
            "X-Hub-Signature-256": request.headers.get("X-Hub-Signature-256", ""),
            "X-Gitea-Signature": request.headers.get("X-Gitea-Signature", ""),
            "X-GitHub-Hookshot": request.headers.get("X-GitHub-Hookshot", ""),
            "X-Gitea-Timestamp": request.headers.get("X-Gitea-Timestamp", ""),
        }
        ip = request.client.host if request.client else "unknown"
        ok, reason = guard.check(ip=ip, headers=headers, body=body)
        if not ok:
            status = 401 if reason == "bad signature" else 403
            return JSONResponse(
                status_code=status,
                content={"error": {"code": "webhook_rejected", "message": reason}},
            )
        delivery = request.headers.get("X-GitHub-Delivery") or request.headers.get(
            "X-Gitea-Delivery"
        )
        if delivery:
            with facade.engine.connect() as connection:
                seen = connection.execute(
                    text("SELECT 1 FROM events WHERE dedup_key=:k"),
                    {"k": f"webhook-delivery:{kind}:{delivery}"},
                ).first()
            if seen:
                return {"status": "duplicate", "delivery": delivery}
        payload = json.loads(body)
        payload["_event"] = request.headers.get("X-GitHub-Event") or request.headers.get(
            "X-Gitea-Event", ""
        )
        adapter = GitHubWebhookAdapter() if kind == "github" else GiteaWebhookAdapter()
        event = adapter.parse(payload)
        result = facade.emit(event)
        if delivery:
            with facade.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO events(id,source,type,category,actor,subject,"
                        "payload_json,dedup_key,received_at,trust,status,summary,link,"
                        "addressed) VALUES(:id,:source,:type,:category,NULL,NULL,"
                        ":payload,:dedup,:received,'verified','accepted',:summary,NULL,0) "
                        "ON CONFLICT(source,dedup_key) DO NOTHING"
                    ),
                    {
                        "id": f"delivery-{delivery}",
                        "source": kind,
                        "type": "webhook.delivery",
                        "category": "new_source",
                        "payload": json.dumps({"delivery": delivery}),
                        "dedup": f"webhook-delivery:{kind}:{delivery}",
                        "received": now(),
                        "summary": f"delivery {delivery}"[:80],
                    },
                )
        return result

    if "github" in guards:

        @app.post("/hooks/github")
        async def hook_github(request: Request):
            return await _receive_webhook("github", request)

    if "gitea" in guards:

        @app.post("/hooks/gitea")
        async def hook_gitea(request: Request):
            return await _receive_webhook("gitea", request)

    @router.get("/health")
    def health(f=Depends(require_agent)):
        with f.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "checks": [{"name": "db", "ok": True}],
            "problems": [],
        }

    @router.post("/events")
    def create_event(
        body: ManualEventInput,
        idempotency_key: str | None = Header(default=None),
        f=Depends(require_agent),
    ):
        request = body.model_dump(exclude_none=True)
        if idempotency_key:
            request["dedup_key"] = idempotency_key
        event = ManualAdapter().parse(request)
        return f.emit(event)

    @router.get("/tasks")
    def list_tasks(
        status: TaskStatus | None = None,
        skill: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        cursor: str | None = None,
        f=Depends(require_agent),
    ):
        return f.list_tasks(
            status=status.value if status else None, skill=skill, limit=limit, cursor=cursor
        )

    @router.get("/system/state")
    def system_state(f=Depends(require_agent)):
        return f.status()

    @router.post("/system:pause")
    def system_pause(_: None = Depends(require_auth)):
        return facade.pause()

    @router.post("/system:resume")
    def system_resume(_: None = Depends(require_auth)):
        return facade.resume()

    # --- M3-02 resource surface ------------------------------------------

    @router.get("/tasks/{task_id}")
    def task_detail(task_id: str, f=Depends(require_agent)):
        result = f.show("tasks", task_id)
        with f.engine.connect() as connection:
            result["runs"] = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM runs WHERE task_id=:id ORDER BY rowid"), {"id": task_id}
                ).mappings()
            ]
            result["decisions"] = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM decisions WHERE task_id=:id ORDER BY rowid"),
                    {"id": task_id},
                ).mappings()
            ]
            result["pull_requests"] = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM pull_requests WHERE task_id=:id"), {"id": task_id}
                ).mappings()
            ]
        return result

    @router.post("/tasks/{task_id}:cancel")
    def task_cancel(task_id: str, f=Depends(require_owner)):
        return f.cancel(task_id)

    @router.post("/tasks/{task_id}:pin")
    def task_pin(task_id: str, f=Depends(require_owner)):
        return f.set_pinned(task_id, True)

    @router.post("/tasks/{task_id}:unpin")
    def task_unpin(task_id: str, f=Depends(require_owner)):
        return f.set_pinned(task_id, False)

    @router.patch("/tasks/{task_id}")
    def task_patch(task_id: str, body: TaskPatch, f=Depends(require_agent)):
        return f.set_priority(task_id, body.priority)

    @router.get("/queue")
    def queue(f=Depends(require_agent)):
        return f.list_tasks(limit=200)

    @router.get("/decisions")
    def decisions(status: str | None = None, f=Depends(require_agent)):
        with f.engine.connect() as connection:
            query = "SELECT * FROM decisions"
            params: dict = {}
            if status:
                query += " WHERE status=:status"
                params["status"] = status
            query += " ORDER BY rowid DESC LIMIT 200"
            return {"items": [dict(r) for r in connection.execute(text(query), params).mappings()]}

    @router.post("/decisions/{decision_id}:answer")
    def decision_answer(decision_id: str, body: DecisionAnswer, f=Depends(require_owner)):
        with facade.engine.connect() as connection:
            row = connection.execute(
                text("SELECT task_id,status FROM decisions WHERE id=:id"), {"id": decision_id}
            ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="decision not found")
        if row[1] != "pending":
            raise HTTPException(status_code=409, detail=f"decision already {row[1]}")
        with facade.engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE decisions SET status='answered',answer=:answer,answered_by='owner'
                  WHERE id=:id
            """),
                {"answer": body.option, "id": decision_id},
            )
            connection.execute(
                text("""
                INSERT INTO audit_log(id,actor,action,at) VALUES(:id,'owner',:action,:at)
            """),
                {
                    "id": f"audit-{decision_id}",
                    "action": f"decision.answer:{body.option}",
                    "at": now(),
                },
            )
        return {"decision_id": decision_id, "answer": body.option, "status": "answered"}

    @router.get("/precedents")
    def precedents(kind: str | None = None, f=Depends(require_agent)):
        from pa.judge.precedents import list_precedents

        with f.engine.connect() as connection:
            items = [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "title": p.title,
                    "content": p.content,
                    "scope": p.scope,
                    "expires_at": p.expires_at,
                }
                for p in list_precedents(connection, kind=kind)
            ]
            return {"items": items}

    @router.post("/precedents")
    def precedent_add(body: dict, _: None = Depends(require_owner)):
        from pa.judge.precedents import add_precedent

        required = {"kind", "title", "content"}
        missing = required - set(body)
        if missing:
            raise HTTPException(status_code=422, detail=f"missing fields: {sorted(missing)}")
        with facade.engine.begin() as connection:
            pid = add_precedent(
                connection,
                kind=body["kind"],
                title=body["title"],
                content=body["content"],
                scope=body.get("scope", "global"),
                quote=body.get("quote", ""),
            )
            connection.execute(
                text("""
                INSERT INTO audit_log(id,actor,action,at) VALUES(:id,'owner',:action,:at)
            """),
                {"id": f"audit-{pid}", "action": "precedent.add", "at": now()},
            )
        return {"id": pid}

    @router.get("/config/{file_name}")
    def config_read(file_name: str, f=Depends(require_agent)):
        import yaml

        target = f.config_dir / file_name
        if not target.exists():
            raise HTTPException(status_code=404, detail="config file not found")
        return {"file": file_name, "value": yaml.safe_load(target.read_text())}

    @router.put("/config/{file_name}")
    def config_write(file_name: str, body: ConfigWrite, f=Depends(require_owner)):
        from pa.config.versions import ConfigConflict, save_versioned

        try:
            with f.engine.begin() as connection:
                result = save_versioned(
                    connection,
                    f.config_dir,
                    file_name=file_name,
                    value=body.value,
                    base_revision=body.base_revision,
                    origin="console",
                    actor="owner",
                )
        except ConfigConflict as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "config_conflict",
                        "message": str(exc),
                        "expected_revision": exc.expected_revision,
                    }
                },
            )
        return result

    @router.get("/config/{file_name}/versions")
    def config_versions(file_name: str, f=Depends(require_agent)):
        from pa.config.versions import versions_of

        with f.engine.connect() as connection:
            return {"items": versions_of(connection, file_name)}

    @router.get("/effects")
    def effects(status: str | None = None, f=Depends(require_agent)):
        with f.engine.connect() as connection:
            query = "SELECT * FROM effects"
            params: dict = {}
            if status:
                query += " WHERE status=:status"
                params["status"] = status
            query += " ORDER BY rowid DESC LIMIT 200"
            return {"items": [dict(r) for r in connection.execute(text(query), params).mappings()]}

    @router.post("/effects/{effect_id}:retry")
    def effect_retry(effect_id: str, f=Depends(require_owner)):
        return f.effect_retry(effect_id)

    @router.post("/effects/{effect_id}:drop")
    def effect_drop(effect_id: str, f=Depends(require_owner)):
        return f.effect_drop(effect_id)

    @router.get("/observations")
    def observations(rule: str | None = None, f=Depends(require_agent)):
        with f.engine.connect() as connection:
            query = "SELECT * FROM observations"
            params: dict = {}
            if rule:
                query += " WHERE rule_id=:rule"
                params["rule"] = rule
            query += " ORDER BY rowid DESC LIMIT 200"
            return {"items": [dict(r) for r in connection.execute(text(query), params).mappings()]}

    app.include_router(router)
    return app
