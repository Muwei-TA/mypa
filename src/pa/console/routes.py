"""Console session routes: login, logout, session-scoped reads (M3-04).

Mounted at /console by create_app when a LoginService is provided. The
console surface is separate from the bearer-token REST API: browsers get a
signed session cookie (HttpOnly, SameSite=Strict) and must echo the CSRF
token on mutating requests.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field

from pa.config.schema import Model
from pa.console.auth import SESSION_COOKIE, cookie_attributes
from pa.console.overview import overview


class LoginInput(Model):
    password: str = Field(min_length=1, max_length=256)
    totp: str = Field(min_length=6, max_length=8)


def build_console_router(login_service, facade) -> APIRouter:
    router = APIRouter(prefix="/console")

    def current_session(request: Request):
        signed = request.cookies.get(SESSION_COOKIE)
        session = login_service.verify_session(signed)
        if session is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return session

    def require_csrf(request: Request, session):
        presented = request.headers.get("X-CSRF-Token")
        if not login_service.check_csrf(session, presented):
            raise HTTPException(status_code=403, detail="csrf check failed")

    @router.post("/login", response_class=JSONResponse)
    def login(body: LoginInput):
        session = login_service.login(password=body.password, totp=body.totp)
        if session is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        response = JSONResponse({"status": "ok", "csrf": session.csrf})
        response.set_cookie(
            SESSION_COOKIE, login_service.sign_session(session), **cookie_attributes()
        )
        return response

    @router.post("/logout", response_class=JSONResponse)
    def logout(request: Request):
        session = current_session(request)
        require_csrf(request, session)
        response = JSONResponse({"status": "ok"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/session", response_class=JSONResponse)
    def session_info(request: Request):
        session = current_session(request)
        return {"status": "ok", "expires_at": session.expires_at, "csrf": session.csrf}

    @router.get("/overview", response_class=JSONResponse)
    def console_overview(request: Request):
        current_session(request)
        with facade.engine.connect() as connection:
            return overview(connection)

    @router.get("/reasons.js", response_class=PlainTextResponse)
    def reasons_catalog(request: Request):
        current_session(request)
        from pa.console.reasons import TRANSLATIONS

        lines = ["const REASON_TRANSLATIONS = {"]
        for code, text in sorted(TRANSLATIONS.items()):
            lines.append(f'  "{code}": "{text}",')
        lines.append("};")
        return PlainTextResponse("\n".join(lines))

    return router
