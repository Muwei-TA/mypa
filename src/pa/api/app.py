"""FastAPI application and M1-18 basic REST endpoints.

All owner endpoints require a configured Bearer token, including on loopback.
Only /healthz is public. The full resource surface lands in M3-02.
"""

import secrets

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import text

from pa.common import PAError
from pa.config.schema import Model
from pa.ingress.manual import ManualAdapter
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


def create_app(facade, *, api_token: str | None = None) -> FastAPI:
    app = FastAPI(
        title="PA Automation", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    router = APIRouter(prefix="/api/v1")

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
