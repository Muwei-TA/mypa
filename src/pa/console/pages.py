"""Console HTML pages (M3-09, PRD §20.3).

Jinja2 templates served under /console with the session guard: no cookie,
no page (redirect to /console/login-page). Destructive buttons carry
data-confirm for the browser double-check; page help text stays one line
with the rest behind (?) tooltips.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from pa.console.auth import SESSION_COOKIE
from pa.console.overview import overview
from pa.console.reasons import stage_label, translate_reasons

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def build_pages_router(login_service, facade) -> APIRouter:
    router = APIRouter(prefix="/console", include_in_schema=False)

    def guard(request: Request):
        session = login_service.verify_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(status_code=307, detail="not signed in")
        return session

    @router.get("/", response_class=HTMLResponse)
    @router.get("/overview-page", response_class=HTMLResponse)
    def overview_page(request: Request):
        guard(request)
        with facade.engine.connect() as connection:
            data = overview(connection)
        return templates.TemplateResponse(
            request,
            "overview.html",
            {"data": data, "stage_label": stage_label},
        )

    @router.get("/login-page", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {})

    @router.get("/queue-page", response_class=HTMLResponse)
    def queue_page(request: Request):
        guard(request)
        tasks = facade.list_tasks(limit=200)
        return templates.TemplateResponse(request, "queue.html", {"tasks": tasks})

    @router.get("/tasks-page/{task_id}", response_class=HTMLResponse)
    def task_page(request: Request, task_id: str):
        guard(request)
        result = facade.show("tasks", task_id)
        with facade.engine.connect() as connection:
            timeline = [
                {**dict(row), "reasons": translate_reasons(row["reason_json"])}
                for row in connection.execute(
                    text("SELECT * FROM task_events WHERE task_id=:id ORDER BY rowid"),
                    {"id": task_id},
                ).mappings()
            ]
            runs = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM runs WHERE task_id=:id ORDER BY attempt"),
                    {"id": task_id},
                ).mappings()
            ]
            decisions = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM decisions WHERE task_id=:id ORDER BY rowid"),
                    {"id": task_id},
                ).mappings()
            ]
            prs = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM pull_requests WHERE task_id=:id"),
                    {"id": task_id},
                ).mappings()
            ]
            costs = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM cost_ledger WHERE task_id=:id"),
                    {"id": task_id},
                ).mappings()
            ]
        return templates.TemplateResponse(
            request,
            "task_detail.html",
            {
                "task": result,
                "timeline": timeline,
                "runs": runs,
                "decisions": decisions,
                "pull_requests": prs,
                "costs": costs,
            },
        )

    @router.get("/decisions-page", response_class=HTMLResponse)
    def decisions_page(request: Request, status: str = "pending"):
        import json

        from pa.hub.decisions import LEGACY_OPTIONS

        guard(request)
        with facade.engine.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM decisions WHERE status=:s ORDER BY rowid DESC LIMIT 200"),
                    {"s": status},
                ).mappings()
            ]
        labels = {
            "approve": "同意",
            "reject": "拒绝",
            "retry": "重试",
            "retry_other_engine": "换引擎重试",
            "take_over": "我接手",
            "one_more_round": "再给一轮",
        }
        for row in rows:
            options = json.loads(row["options_json"]) if row["options_json"] else LEGACY_OPTIONS
            row["options"] = [{"id": o, "label": labels.get(o, o)} for o in options]
        return templates.TemplateResponse(
            request, "decisions.html", {"decisions": rows, "status": status}
        )

    @router.get("/engines-page", response_class=HTMLResponse)
    def engines_page(request: Request):
        guard(request)
        with facade.engine.connect() as connection:
            engines = [
                dict(row)
                for row in connection.execute(text("SELECT * FROM engine_health")).mappings()
            ]
        return templates.TemplateResponse(request, "engines.html", {"engines": engines})

    @router.get("/observations-page", response_class=HTMLResponse)
    def observations_page(request: Request):
        guard(request)
        items = facade.list("observations", limit=200)
        return templates.TemplateResponse(request, "observations.html", {"items": items})

    @router.get("/cost-page", response_class=HTMLResponse)
    def cost_page(request: Request):
        guard(request)
        with facade.engine.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    text("""
                    SELECT date(at) AS day, skill_id, COUNT(*) AS tasks,
                           SUM(cost_cny) AS cost_cny
                    FROM cost_ledger GROUP BY day, skill_id ORDER BY day DESC LIMIT 200
                """)
                ).mappings()
            ]
        return templates.TemplateResponse(request, "cost.html", {"rows": rows})

    @router.get("/config-page", response_class=HTMLResponse)
    def config_page(request: Request, file_name: str = "pa.yaml"):
        guard(request)
        from pa.config.versions import versions_of

        target = facade.config_dir / file_name
        content = target.read_text() if target.exists() else ""
        with facade.engine.connect() as connection:
            versions = versions_of(connection, file_name)
        return templates.TemplateResponse(
            request,
            "config.html",
            {"file_name": file_name, "content": content, "versions": versions},
        )

    @router.get("/audit-page", response_class=HTMLResponse)
    def audit_page(request: Request):
        guard(request)
        items = facade.list("audit_log", limit=200)
        return templates.TemplateResponse(request, "audit.html", {"items": items})

    @router.get("/search-page", response_class=HTMLResponse)
    def search_page(request: Request, q: str = ""):
        guard(request)
        from pa.console.search import search

        results = {"query": q, "items": []}
        if q:
            with facade.engine.connect() as connection:
                results = search(connection, q)
        return templates.TemplateResponse(request, "search.html", {"results": results})

    return router
