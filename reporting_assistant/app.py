"""Web API and portal (proposal section 7: user portal + report generator).

Run with:  uvicorn reporting_assistant.app:app --reload
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .assistant import Assistant
from .auth import UnknownUser, User, demo_user
from .config import PACKAGE_DIR, Settings, get_settings
from .exporters import EXPORTERS, render_chart_png
from .llm import Planner
from .planners import make_planner
from .query_service import QueryService
from .refresh import last_refresh, refresh_reporting_store
from .sample_data import build_sample_erp
from .semantic import load_semantic_layer
from .store import AppStore


class LoginRequest(BaseModel):
    username: str


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class FavouriteRequest(BaseModel):
    name: str
    request: str


def create_app(settings: Settings | None = None, planner: Planner | None = None) -> FastAPI:
    settings = settings or get_settings()
    layer = load_semantic_layer(settings.semantic_layer_path)

    # First run: create the sample ERP and the reporting copy.
    if not settings.erp_db.exists():
        build_sample_erp(settings.erp_db)
    if not settings.reporting_db.exists():
        refresh_reporting_store(settings.erp_db, settings.reporting_db)

    store = AppStore(settings.app_db)
    if planner is None:
        planner = make_planner(settings)
    assistant = Assistant(
        layer=layer,
        queries=QueryService(settings.reporting_db, layer, settings.query_timeout_seconds, settings.max_rows),
        planner=planner,
        store=store,
        reporting_db=settings.reporting_db,
        freshness_hours={"inventory": settings.inventory_refresh_hours, "finance": settings.finance_refresh_hours},
    )
    secret = os.getenv("RA_SECRET", "").encode() or secrets.token_bytes(32)

    def sign(username: str) -> str:
        return f"{username}.{hmac.new(secret, username.encode(), hashlib.sha256).hexdigest()}"

    def current_user(authorization: str = Header(default="")) -> User:
        token = authorization.removeprefix("Bearer ").strip()
        username, _, _signature = token.rpartition(".")
        if not username or not hmac.compare_digest(sign(username), token):
            raise HTTPException(401, "Please sign in.")
        try:
            return demo_user(layer, username)
        except UnknownUser:
            raise HTTPException(401, "Please sign in.") from None

    def admin_user(user: User = Depends(current_user)) -> User:
        if not user.role.can_administer:
            raise HTTPException(403, "Administrators only.")
        return user

    def own_report(report_id: str, user: User) -> dict:
        report = store.get_report(report_id)
        if not report or (report["username"] != user.username and not user.role.can_administer):
            raise HTTPException(404, "Report not found.")
        return report

    app = FastAPI(title="AI Reporting Assistant", version="0.1.0")
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(PACKAGE_DIR / "static" / "index.html")

    @app.get("/api/users")
    def users():
        return [{"username": u["username"], "name": u["name"], "role": layer.roles[u["role"]].label}
                for u in layer.demo_users]

    @app.post("/api/login")
    def login(body: LoginRequest):
        try:
            user = demo_user(layer, body.username)
        except UnknownUser:
            raise HTTPException(401, "Unknown user.") from None
        return {"token": sign(user.username), "user": _user_json(user)}

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        return _user_json(user)

    @app.get("/api/status")
    def status(user: User = Depends(current_user)):
        return {"data_as_of": last_refresh(settings.reporting_db)}

    @app.get("/api/templates")
    def templates(user: User = Depends(current_user)):
        return [t for t in layer.templates if t["domain"] in user.role.domains]

    @app.post("/api/chat")
    def chat(body: ChatRequest, user: User = Depends(current_user)):
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "Type a request.")
        session_id = body.session_id or uuid.uuid4().hex
        reply = assistant.handle(user, session_id, message[:2000])
        return {"session_id": session_id, **reply.to_dict()}

    @app.get("/api/reports/{report_id}")
    def get_report(report_id: str, user: User = Depends(current_user)):
        return own_report(report_id, user)

    @app.get("/api/reports/{report_id}/export/{fmt}")
    def export(report_id: str, fmt: str, user: User = Depends(current_user)):
        if fmt not in EXPORTERS:
            raise HTTPException(400, "Format must be pdf, docx or xlsx.")
        report = own_report(report_id, user)
        build, media_type = EXPORTERS[fmt]
        filename = "".join(c if c.isalnum() else "_" for c in report["title"])[:60] or "report"
        return Response(build(report), media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{filename}.{fmt}"'})

    @app.get("/api/reports/{report_id}/chart.png")
    def chart_png(report_id: str, user: User = Depends(current_user)):
        png = render_chart_png(own_report(report_id, user))
        if png is None:
            raise HTTPException(404, "This report has no chart.")
        return Response(png, media_type="image/png")

    @app.get("/api/favourites")
    def favourites(user: User = Depends(current_user)):
        return store.favourites(user.username)

    @app.post("/api/favourites")
    def add_favourite(body: FavouriteRequest, user: User = Depends(current_user)):
        return {"id": store.save_favourite(user.username, body.name[:120], body.request[:2000])}

    @app.delete("/api/favourites/{favourite_id}")
    def delete_favourite(favourite_id: str, user: User = Depends(current_user)):
        store.delete_favourite(user.username, favourite_id)
        return {"ok": True}

    @app.get("/api/admin/audit")
    def audit(user: User = Depends(admin_user)):
        return store.audit_entries()

    @app.post("/api/admin/refresh")
    def refresh(user: User = Depends(admin_user)):
        counts = refresh_reporting_store(settings.erp_db, settings.reporting_db)
        return {"tables": counts, "data_as_of": last_refresh(settings.reporting_db)}

    return app


def _user_json(user: User) -> dict:
    return {"username": user.username, "name": user.name, "role": user.role.label,
            "can_administer": user.role.can_administer, "scope": user.describe_scope()}


def __getattr__(name: str):
    # `uvicorn reporting_assistant.app:app` builds the app on first access, so
    # importing this module (e.g. in tests) has no side effects.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
