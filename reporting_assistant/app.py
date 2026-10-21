"""Web API and portal (proposal section 7: user portal + report generator).

Run with:  python -m uvicorn reporting_assistant.app:app --reload
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .assistant import Assistant
from .auth import UnknownUser, User, demo_user
from .config import PACKAGE_DIR, Settings, get_settings
from .documents import (TEMPLATES, DemoExtractor, DocumentError, DocumentService, LocalApprovalWorkflow,
                        OpenAICompatibleExtractor, PortalApprovalWorkflow, ReportingStoreMasterData)
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


class ConfirmRequest(BaseModel):
    pending_id: str


class FlagRequest(BaseModel):
    reason: str = ""


class ReviewRequest(BaseModel):
    data: dict
    accept_warnings: bool = False


class FavouriteRequest(BaseModel):
    name: str
    request: str


def make_extractor(settings: Settings, planner: Planner | None = None):
    """Vision model for documents: same server as the reporting model (proposal)."""
    provider = "demo" if settings.demo_mode else settings.provider
    model = settings.document_model or settings.llm_model
    if provider in ("vllm", "gemini", "groq", "openrouter", "grok", "ollama") and model:
        try:
            return OpenAICompatibleExtractor(provider, model)
        except Exception:
            pass
    return DemoExtractor()


def create_app(settings: Settings | None = None, planner: Planner | None = None, extractor=None,
               workflow=None) -> FastAPI:
    settings = settings or get_settings()
    layer = load_semantic_layer(settings.semantic_layer_path)
    store = AppStore(settings.app_db)
    if planner is None:
        planner = make_planner(settings)

    # One assistant per client, each connected only to that client's own
    # database. The client comes from the login token, never from the chat.
    assistants: dict[str, Assistant] = {}
    assistants_lock = threading.Lock()

    def client_paths(client_id: str) -> tuple[Path, Path]:
        folder = settings.client_dir(client_id, layer.default_client)
        return folder / "erp.db", folder / "reporting.db"

    def assistant_for(user: User) -> Assistant:
        with assistants_lock:
            if user.client_id not in assistants:
                erp_db, reporting_db = client_paths(user.client_id)
                # First run: create the sample ERP and the reporting copy.
                if not erp_db.exists():
                    build_sample_erp(erp_db, seed=int(layer.clients[user.client_id].get("sample_seed", 42)))
                if not reporting_db.exists():
                    refresh_reporting_store(erp_db, reporting_db)
                client = layer.clients[user.client_id]
                assistants[user.client_id] = Assistant(
                    layer=layer,
                    queries=QueryService(reporting_db, layer, settings.query_timeout_seconds, settings.max_rows,
                                         dialect=settings.sql_dialect),
                    planner=planner,
                    store=store,
                    reporting_db=reporting_db,
                    freshness_hours={"inventory": settings.inventory_refresh_hours,
                                     "finance": settings.finance_refresh_hours},
                    client_id=user.client_id,
                    confirm_new_requests=settings.confirm_new_requests,
                    defer_summary=settings.defer_summary,
                    monthly_token_quota=int(client.get("monthly_token_quota", settings.monthly_token_quota)),
                    quota_warning_ratio=settings.quota_warning_ratio,
                )
            return assistants[user.client_id]

    # Build the default client's data now, so the first request is quick.
    assistant_for(demo_user(layer, layer.demo_users[0]["username"]))

    if workflow is None:
        workflow = (PortalApprovalWorkflow(settings.portal_api_url, os.getenv("RA_PORTAL_API_TOKEN", ""))
                    if settings.portal_api_url else LocalApprovalWorkflow(settings.data_dir / "approval_workflow.db"))
    documents = DocumentService(
        store=store,
        extractor=extractor or make_extractor(settings, planner),
        workflow=workflow,
        master_for=lambda client_id: ReportingStoreMasterData(client_paths(client_id)[1]),
        files_dir=settings.documents_dir,
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
        if not report or report.get("client_id", layer.default_client) != user.client_id \
                or (report["username"] != user.username and not user.role.can_administer):
            raise HTTPException(404, "Report not found.")
        return report

    def document_user(user: User = Depends(current_user)) -> User:
        if not user.role.can_capture_documents:
            raise HTTPException(403, "Your role cannot capture documents.")
        return user

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
        return {"data_as_of": last_refresh(assistant_for(user).reporting_db)}

    @app.get("/api/templates")
    def templates(user: User = Depends(current_user)):
        return [t for t in layer.templates if t["domain"] in user.role.domains]

    @app.post("/api/chat")
    def chat(body: ChatRequest, user: User = Depends(current_user)):
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "Type a request.")
        session_id = body.session_id or uuid.uuid4().hex
        reply = assistant_for(user).handle(user, session_id, message[:2000])
        return {"session_id": session_id, **reply.to_dict()}

    @app.post("/api/chat/confirm")
    def confirm(body: ConfirmRequest, user: User = Depends(current_user)):
        reply = assistant_for(user).confirm(user, body.pending_id)
        return reply.to_dict()

    @app.get("/api/reports/{report_id}/summary")
    def summary(report_id: str, user: User = Depends(current_user)):
        """Second step: the short summary, after the table and chart are shown."""
        report = own_report(report_id, user)
        return {"narrative": assistant_for(user).summarize(report)}

    @app.post("/api/reports/{report_id}/flag")
    def flag(report_id: str, body: FlagRequest, user: User = Depends(current_user)):
        report = own_report(report_id, user)
        flag_id = store.add_flag(client_id=user.client_id, username=user.username, report_id=report_id,
                                 request=report["request"], sql=report["sql"], reason=body.reason[:1000])
        return {"id": flag_id}

    @app.get("/api/usage")
    def usage(user: User = Depends(current_user)):
        quota = assistant_for(user).quota_status(user.client_id)
        return {"month": store.monthly_usage(user.client_id), "quota": quota}

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
        return store.audit_entries(client_id=user.client_id)

    @app.get("/api/admin/usage")
    def usage_log(user: User = Depends(admin_user)):
        return store.usage_entries(user.client_id)

    @app.get("/api/admin/flags")
    def flags(user: User = Depends(admin_user)):
        return store.flags(user.client_id)

    @app.post("/api/admin/flags/{flag_id}/close")
    def close_flag(flag_id: str, user: User = Depends(admin_user)):
        if not any(f["id"] == flag_id for f in store.flags(user.client_id)):
            raise HTTPException(404, "Flag not found.")
        store.set_flag_status(flag_id, "closed")
        return {"ok": True}

    @app.get("/api/admin/verified")
    def verified(user: User = Depends(admin_user)):
        return store.verified_reports(user.client_id)

    @app.post("/api/admin/verified/{report_id}")
    def verify(report_id: str, user: User = Depends(admin_user)):
        """Our team checked this report: repeat questions will reuse its query."""
        report = own_report(report_id, user)
        try:
            return {"id": assistant_for(user).verify(report, user.username)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/admin/verified/{verified_id}")
    def retire_verified(verified_id: str, user: User = Depends(admin_user)):
        store.retire_verified(user.client_id, verified_id)
        return {"ok": True}

    @app.post("/api/admin/refresh")
    def refresh(user: User = Depends(admin_user)):
        erp_db, reporting_db = client_paths(user.client_id)
        counts = refresh_reporting_store(erp_db, reporting_db)
        return {"tables": counts, "data_as_of": last_refresh(reporting_db)}

    # Document capture

    @app.get("/api/documents/types")
    def document_types(user: User = Depends(document_user)):
        return [{"id": key, "label": key.replace("_", " ").capitalize(), "schema": model.model_json_schema()}
                for key, model in TEMPLATES.items()]

    @app.post("/api/documents")
    def upload_document(background: BackgroundTasks, doc_type: str = Form(...), file: UploadFile = File(...),
                        user: User = Depends(document_user)):
        try:
            document_id = documents.upload(user, doc_type, file.filename or "document", file.file.read())
        except DocumentError as exc:
            raise HTTPException(400, str(exc)) from None
        background.add_task(documents.process, document_id)  # queued; does not slow down reports
        return {"id": document_id, "status": "queued"}

    @app.get("/api/documents")
    def list_documents(user: User = Depends(document_user)):
        return store.documents(user.client_id, None if user.role.can_administer else user.username)

    def own_document(document_id: str, user: User) -> dict:
        doc = store.get_document(document_id, user.client_id)
        if not doc or (doc["uploaded_by"] != user.username and not user.role.can_administer):
            raise HTTPException(404, "Document not found.")
        return doc

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str, user: User = Depends(document_user)):
        doc = own_document(document_id, user)
        doc.pop("file_path", None)
        return doc

    @app.get("/api/documents/{document_id}/file")
    def document_file(document_id: str, user: User = Depends(document_user)):
        doc = own_document(document_id, user)
        return FileResponse(doc["file_path"], filename=doc["filename"])

    @app.post("/api/documents/{document_id}/review")
    def review_document(document_id: str, body: ReviewRequest, user: User = Depends(document_user)):
        own_document(document_id, user)
        try:
            doc = documents.review(user, document_id, body.data, body.accept_warnings)
        except DocumentError as exc:
            raise HTTPException(400, str(exc)) from None
        doc.pop("file_path", None)
        return doc

    return app


def _user_json(user: User) -> dict:
    return {"username": user.username, "name": user.name, "role": user.role.label,
            "can_administer": user.role.can_administer, "can_capture_documents": user.role.can_capture_documents,
            "client_id": user.client_id, "scope": user.describe_scope()}


def __getattr__(name: str):
    # `uvicorn reporting_assistant.app:app` builds the app on first access, so
    # importing this module (e.g. in tests) has no side effects.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
