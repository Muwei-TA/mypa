"""FastAPI application and M1-18 basic REST endpoints.

All owner endpoints require a configured Bearer token, including on loopback.
Only /healthz (and the configured webhook endpoints) are public. The full
resource surface lands in M3-02.
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


class ManualEventInput(Model):
    type: str = Field(min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=80)
    payload: dict = Field(default_factory=dict)
    subject: str | None = None


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
    webhook_secrets: dict[str, str] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="PA Automation", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    router = APIRouter(prefix="/api/v1")
    guards = {k: WebhookGuard(secret=v) for k, v in (webhook_secrets or {}).items()}

    def require_auth(authorization: str | None = Header(default=None)) -> None:
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
    def health(_: None = Depends(require_auth)):
        with facade.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "checks": [{"name": "db", "ok": True}],
            "problems": [],
        }

    @router.post("/events")
    def create_event(
        body: ManualEventInput,
        idempotency_key: str | None = Header(default=None),
        _: None = Depends(require_auth),
    ):
        request = body.model_dump(exclude_none=True)
        if idempotency_key:
            request["dedup_key"] = idempotency_key
        event = ManualAdapter().parse(request)
        return facade.emit(event)

    @router.get("/tasks")
    def list_tasks(
        status: TaskStatus | None = None,
        skill: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        cursor: str | None = None,
        _: None = Depends(require_auth),
    ):
        return facade.list_tasks(
            status=status.value if status else None, skill=skill, limit=limit, cursor=cursor
        )

    @router.get("/system/state")
    def system_state(_: None = Depends(require_auth)):
        return facade.status()

    @router.post("/system:pause")
    def system_pause(_: None = Depends(require_auth)):
        return facade.pause()

    @router.post("/system:resume")
    def system_resume(_: None = Depends(require_auth)):
        return facade.resume()

    app.include_router(router)
    return app
