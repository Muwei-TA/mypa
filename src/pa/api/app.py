"""FastAPI application and M1-18 basic REST endpoints.

M1-18 scope covers health, event ingestion, task listing and the system
pause/resume controls. The full resource surface lands in M3-02. When no
``api_token`` is supplied the app runs in local loopback single-user mode;
providing a token enables Bearer authentication.
"""

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from pa.common import PAError
from pa.ingress.manual import ManualAdapter

_STATUS_CODE = {
    "not_found": 404,
    "forbidden": 403,
    "conflict": 409,
    "bad_request": 400,
}


def create_app(facade, *, api_token: str | None = None) -> FastAPI:
    app = FastAPI(title="PA Automation", version="0.1.0")
    router = APIRouter(prefix="/api/v1")

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if api_token is None:
            return
        if authorization != f"Bearer {api_token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.exception_handler(PAError)
    def handle_pa_error(request, exc):
        return JSONResponse(
            status_code=_STATUS_CODE.get(exc.code, 400),
            content={"detail": str(exc)},
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
        body: dict,
        idempotency_key: str | None = Header(default=None),
        _: None = Depends(require_auth),
    ):
        request = dict(body)
        if idempotency_key:
            request["dedup_key"] = idempotency_key
        event = ManualAdapter().parse(request)
        return facade.emit(event)

    @router.get("/tasks")
    def list_tasks(
        status: str | None = None,
        skill: str | None = None,
        _: None = Depends(require_auth),
    ):
        items = facade.list("tasks", limit=500)["items"]
        if status:
            items = [row for row in items if row["status"] == status]
        if skill:
            items = [row for row in items if row["skill_id"] == skill]
        return {"items": items}

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
