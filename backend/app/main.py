"""Authenticated HTTP API. One enterprise per installation; no demonstration mode."""

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, or_, func, inspect as sa_inspect, text
from sqlalchemy.exc import IntegrityError, DataError, SQLAlchemyError
from sqlalchemy.orm import Session
from . import models as m, schemas as s
from .auth import (
    Actor,
    current_actor,
    admin_actor,
    get_session,
    utcnow,
    verify_password,
    hash_password,
    require_origin,
    rate_limit_login,
    new_session,
    COOKIE_NAME,
)
from .config import get_settings
from .repositories import AccessRepository, AuthorizationError, NotFound

app = FastAPI(
    title="V-OptimAIse",
    version="2.0.0",
    docs_url=None,
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
from .middleware import RequestSizeLimit

app.add_middleware(RequestSizeLimit)


def row(obj, exclude=()):
    hidden = {"password_hash", "token_hash", "csrf_token"} | set(exclude)
    data = {
        col.key: getattr(obj, col.key)
        for col in sa_inspect(obj).mapper.column_attrs
        if col.key not in hidden
    }
    if "model" in data:
        # Clients see the model level only, never the provider model name.
        data["model"] = get_settings().public_model(data["model"])
    return data


def public_user(user):
    return {
        key: getattr(user, key)
        for key in ("id", "email", "name", "is_admin", "active", "clearance")
    }


def audit(actor, action, resource_type, resource_id, details=None):
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
    )


def private(actor, model, rid):
    obj = actor.db.get(model, rid)
    if obj is None or obj.owner_id != actor.user.id:
        raise HTTPException(404, "Private resource not found")
    return obj


def access(actor):
    return AccessRepository(actor.db)


def workspace_payload(actor, w):
    membership = actor.db.get(m.Membership, (w.id, actor.user.id))
    return {**row(w), "membership_role": membership.role if membership else None}


def project_check(actor, pid):
    if pid:
        return access(actor).project(actor.user, pid)


def commit(actor, obj=None):
    actor.db.commit()
    return row(obj) if obj is not None else {"ok": True}


@app.exception_handler(AuthorizationError)
async def forbidden(request, exc):
    return JSONResponse(status_code=403, content={"detail": str(exc) or "Access denied"})


@app.exception_handler(NotFound)
async def missing(request, exc):
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse(
        status_code=409,
        content={
            "detail": "This change conflicts with an existing record or relationship. Refresh and try again."
        },
    )


@app.exception_handler(DataError)
async def invalid_data(request, exc):
    return JSONResponse(status_code=422, content={"detail": "Invalid resource value"})


@app.exception_handler(SQLAlchemyError)
async def database_failure(request, exc):
    import logging

    logging.getLogger("voptimai.api").error(
        "Database request failed: error_type=%s", type(exc).__name__
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The database operation could not complete. Please retry or contact the administrator."
        },
    )


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health/live")
def live():
    return {"status": "ok"}


@app.get("/api/health/ready")
def ready(db: Session = Depends(get_session)):
    try:
        db.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
    except Exception:
        raise HTTPException(503, "Database or migrations are not ready")
    return {"status": "ready"}


@app.post("/api/auth/login")
def login(data: s.Login, request: Request, response: Response, db: Session = Depends(get_session)):
    require_origin(request)
    email = data.email.strip().lower()
    rate_limit_login(db, email, request.client.host if request.client else "unknown")
    user = db.scalar(select(m.User).where(m.User.email == email))
    valid = verify_password(data.password, user.password_hash if user else None)
    if not user or not user.active or user.is_service or not valid:
        raise HTTPException(401, "Invalid email or password")
    auth_session, token = new_session(db, user)
    db.add(
        m.AuditEvent(
            actor_id=user.id,
            action="signed_in",
            resource_type="user",
            resource_id=user.id,
            details={},
        )
    )
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="strict",
        max_age=get_settings().session_ttl_hours * 3600,
        path="/",
    )
    return {"user": public_user(user), "csrf_token": auth_session.csrf_token}


@app.get("/api/auth/me")
def me(actor: Actor = Depends(current_actor)):
    return {"user": public_user(actor.user), "csrf_token": actor.session.csrf_token}


@app.post("/api/auth/logout")
def logout(response: Response, actor: Actor = Depends(current_actor)):
    actor.db.delete(actor.session)
    actor.db.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(data: s.Password, response: Response, actor: Actor = Depends(current_actor)):
    if not verify_password(data.current_password, actor.user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    actor.user.password_hash = hash_password(data.new_password)
    actor.db.execute(delete(m.AuthSession).where(m.AuthSession.user_id == actor.user.id))
    audit(actor, "password_changed", "user", actor.user.id)
    actor.db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True, "detail": "Password changed. Sign in again."}


@app.get("/api/bootstrap")
def bootstrap(actor: Actor = Depends(current_actor)):
    settings = get_settings()
    from .services.governance import GovernanceService

    delegated = GovernanceService(actor.db).delegated_scopes(actor.user)
    return {
        "can_create_workspace": actor.user.is_admin or bool(delegated),
        "delegated_scope_ids": delegated,
        "user": public_user(actor.user),
        "workspaces": workspaces(actor),
        "scopes": scopes(actor),
        "projects": projects(actor),
        "models": settings.model_choices,
        "default_model": settings.public_model(settings.openrouter_default_model),
        "reviewable_scope_ids": [scope.id for scope in access(actor).reviewable_scopes(actor.user)],
        "external_ai_enabled": settings.allow_external_ai and bool(settings.openrouter_api_key),
        "notifications": notifications(actor),
    }


@app.get("/api/directory")
def directory(actor: Actor = Depends(current_actor)):
    return [
        {key: getattr(u, key) for key in ("id", "name", "email", "clearance")}
        for u in actor.db.scalars(
            select(m.User)
            .where(m.User.active.is_(True), m.User.is_service.is_(False))
            .order_by(m.User.name)
            .limit(500)
        )
    ]


@app.get("/api/projects")
def projects(actor: Actor = Depends(current_actor)):
    return [
        row(p)
        for p in actor.db.scalars(
            select(m.Project)
            .where(m.Project.owner_id == actor.user.id)
            .order_by(m.Project.created_at.desc())
            .limit(200)
        )
    ]


@app.post("/api/projects", status_code=201)
def create_project(data: s.ProjectCreate, actor: Actor = Depends(current_actor)):
    obj = m.Project(owner_id=actor.user.id, **data.model_dump())
    actor.db.add(obj)
    return commit(actor, obj)


@app.patch("/api/projects/{rid}")
def update_project(rid: s.Id, data: s.ProjectUpdate, actor: Actor = Depends(current_actor)):
    obj = access(actor).project(actor.user, rid)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, "Fields cannot be null")
        setattr(obj, key, value)
    return commit(actor, obj)


@app.get("/api/notes")
def notes(project_id: s.Id | None = None, q: str = "", actor: Actor = Depends(current_actor)):
    project_check(actor, project_id)
    stmt = select(m.Note).where(m.Note.owner_id == actor.user.id)
    if project_id:
        stmt = stmt.where(m.Note.project_id == project_id)
    if q:
        stmt = stmt.where(
            or_(m.Note.title.ilike("%" + q[:200] + "%"), m.Note.body.ilike("%" + q[:200] + "%"))
        )
    return [row(n) for n in actor.db.scalars(stmt.order_by(m.Note.created_at.desc()).limit(200))]


@app.post("/api/notes", status_code=201)
def create_note(data: s.NoteCreate, actor: Actor = Depends(current_actor)):
    project_check(actor, data.project_id)
    obj = m.Note(
        owner_id=actor.user.id, **{**data.model_dump(), "title": data.title or data.body[:72]}
    )
    actor.db.add(obj)
    return commit(actor, obj)


@app.patch("/api/notes/{rid}")
def update_note(rid: s.Id, data: s.NoteUpdate, actor: Actor = Depends(current_actor)):
    obj = access(actor).note(actor.user, rid)
    changes = data.model_dump(exclude_unset=True)
    project_check(actor, changes.get("project_id"))
    if "project_id" in changes and changes["project_id"] != obj.project_id:
        from .models_personal import NotePlacement

        placement = actor.db.get(NotePlacement, obj.id)
        if placement:
            actor.db.delete(placement)
    for key, value in changes.items():
        if value is None and key != "project_id":
            raise HTTPException(422, "Only project_id can be null")
        setattr(obj, key, value)
    return commit(actor, obj)


@app.get("/api/memories")
def memories(actor: Actor = Depends(current_actor)):
    return [
        row(n)
        for n in actor.db.scalars(
            select(m.Memory)
            .where(m.Memory.owner_id == actor.user.id, m.Memory.forgotten_at.is_(None))
            .order_by(m.Memory.created_at.desc())
            .limit(200)
        )
    ]


@app.post("/api/memories", status_code=201)
def create_memory(data: s.MemoryCreate, actor: Actor = Depends(current_actor)):
    access(actor).note(actor.user, data.note_id)
    obj = m.Memory(owner_id=actor.user.id, **data.model_dump())
    actor.db.add(obj)
    return commit(actor, obj)


@app.patch("/api/memories/{rid}")
def update_memory(rid: s.Id, data: s.MemoryUpdate, actor: Actor = Depends(current_actor)):
    obj = private(actor, m.Memory, rid)
    if obj.forgotten_at:
        raise HTTPException(404, "Memory not found")
    obj.text = data.text
    return commit(actor, obj)


@app.delete("/api/memories/{rid}")
def forget_memory(rid: s.Id, actor: Actor = Depends(current_actor)):
    obj = private(actor, m.Memory, rid)
    obj.forgotten_at, obj.text = utcnow(), ""
    return commit(actor)


@app.get("/api/reminders")
def reminders(actor: Actor = Depends(current_actor)):
    return [
        row(n)
        for n in actor.db.scalars(
            select(m.Reminder)
            .where(m.Reminder.owner_id == actor.user.id)
            .order_by(m.Reminder.due_at)
            .limit(200)
        )
    ]


@app.post("/api/reminders", status_code=201)
def create_reminder(data: s.ReminderCreate, actor: Actor = Depends(current_actor)):
    access(actor).note(actor.user, data.note_id)
    obj = m.Reminder(owner_id=actor.user.id, status="open", **data.model_dump())
    actor.db.add(obj)
    return commit(actor, obj)


@app.patch("/api/reminders/{rid}")
def update_reminder(rid: s.Id, data: s.ReminderUpdate, actor: Actor = Depends(current_actor)):
    obj = private(actor, m.Reminder, rid)
    if data.status in ("open", "snoozed", "waiting"):
        if data.due_at is None:
            raise HTTPException(422, "Specify the next check-in time")
        obj.due_at, obj.notified_at = data.due_at, None
    obj.status = data.status
    return commit(actor, obj)


def scopes(actor):
    # Vocabulary is enterprise directory metadata, not permission to underlying content.
    return [
        row(n) for n in actor.db.scalars(select(m.Scope).order_by(m.Scope.created_at).limit(1000))
    ]


@app.get("/api/workspaces")
def workspaces(actor: Actor = Depends(current_actor)):
    return [workspace_payload(actor, w) for w in access(actor).accessible_workspaces(actor.user)]


@app.post("/api/workspaces", status_code=201)
def create_workspace(data: s.WorkspaceCreate, actor: Actor = Depends(current_actor)):
    from .services.governance import GovernanceService

    GovernanceService(actor.db).require_scope(actor.user, data.scope_id)
    if not actor.db.get(m.Scope, data.scope_id):
        raise HTTPException(404, "Scope not found")
    if data.classification > actor.user.clearance:
        raise HTTPException(403, "Workspace classification exceeds your clearance")
    obj = m.Workspace(created_by=actor.user.id, **data.model_dump())
    actor.db.add(obj)
    actor.db.flush()
    actor.db.add(m.Membership(workspace_id=obj.id, user_id=actor.user.id, role="manager"))
    audit(actor, "workspace_created", "workspace", obj.id)
    actor.db.commit()
    return workspace_payload(actor, obj)


@app.patch("/api/workspaces/{rid}")
def update_workspace(rid: s.Id, data: s.WorkspaceUpdate, actor: Actor = Depends(current_actor)):
    obj = access(actor).workspace(actor.user, rid, roles=["manager"])
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, "Fields cannot be null")
        setattr(obj, key, value)
    audit(actor, "workspace_configured", "workspace", rid, {"fields": list(data.model_fields_set)})
    return commit(actor, obj)


@app.get("/api/workspaces/{rid}/members")
def members(rid: s.Id, actor: Actor = Depends(current_actor)):
    access(actor).workspace(actor.user, rid)
    return [
        {"user_id": u.id, "name": u.name, "email": u.email, "role": membership.role}
        for membership, u in actor.db.execute(
            select(m.Membership, m.User)
            .join(m.User, m.User.id == m.Membership.user_id)
            .where(m.Membership.workspace_id == rid)
        )
    ]


@app.post("/api/workspaces/{rid}/members")
def add_member(rid: s.Id, data: s.MemberCreate, actor: Actor = Depends(current_actor)):
    actor.db.scalar(select(m.Workspace).where(m.Workspace.id == rid).with_for_update())
    from .services.governance import GovernanceService

    w = actor.db.get(m.Workspace, rid)
    if not w:
        raise HTTPException(404, "Workspace not found")
    GovernanceService(actor.db).require_people_management(actor.user, w)
    user = actor.db.get(m.User, data.user_id)
    if not user or not user.active or user.is_service or user.clearance < w.classification:
        raise HTTPException(403, "Choose an active user with sufficient clearance")
    membership = actor.db.get(m.Membership, (rid, data.user_id))
    if membership and membership.role == "manager" and data.role != "manager":
        managers = list(
            actor.db.scalars(
                select(m.Membership)
                .where(m.Membership.workspace_id == rid, m.Membership.role == "manager")
                .with_for_update()
            )
        )
        if len(managers) <= 1:
            raise HTTPException(409, "Keep at least one workspace manager")
    if membership:
        membership.role = data.role
    else:
        actor.db.add(m.Membership(workspace_id=rid, **data.model_dump()))
    audit(
        actor, "membership_updated", "workspace", rid, {"user_id": data.user_id, "role": data.role}
    )
    return commit(actor)


@app.delete("/api/workspaces/{rid}/members/{user_id}")
def remove_member(rid: s.Id, user_id: s.Id, actor: Actor = Depends(current_actor)):
    actor.db.scalar(select(m.Workspace).where(m.Workspace.id == rid).with_for_update())
    from .services.governance import GovernanceService

    workspace = actor.db.get(m.Workspace, rid)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    GovernanceService(actor.db).require_people_management(actor.user, workspace)
    membership = actor.db.get(m.Membership, (rid, user_id))
    if not membership:
        raise HTTPException(404, "Membership not found")
    if actor.db.get(m.User, user_id).is_service:
        raise HTTPException(
            409,
            "Pause the approved workspace jobs to disable automation; this identity is managed by the platform.",
        )
    managers = list(
        actor.db.scalars(
            select(m.Membership)
            .where(m.Membership.workspace_id == rid, m.Membership.role == "manager")
            .with_for_update()
        )
    )
    if membership.role == "manager" and len(managers) <= 1:
        raise HTTPException(409, "Keep at least one workspace manager")
    actor.db.delete(membership)
    audit(actor, "membership_removed", "workspace", rid, {"user_id": user_id})
    return commit(actor)


from .repositories import WorkspaceRepository
from .services.context import ContextService
from .services.documents import extract_document
from .services.gateway import OpenRouterGateway, select_model
from .services.errors import ServiceError


@app.exception_handler(ServiceError)
async def service_error(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def validate_audience(actor, workspace_id=None, project_id=None, write=False):
    if workspace_id and project_id:
        raise HTTPException(422, "Choose either a workspace or a private project")
    if workspace_id:
        return access(actor).workspace(
            actor.user, workspace_id, roles=["member", "manager"] if write else None
        )
    project_check(actor, project_id)


@app.get("/api/documents")
def documents(
    workspace_id: s.Id | None = None,
    project_id: s.Id | None = None,
    actor: Actor = Depends(current_actor),
):
    validate_audience(actor, workspace_id, project_id)
    return [
        row(d, ("body",))
        for d in WorkspaceRepository(actor.db).documents(
            actor.user, workspace_id=workspace_id, project_id=project_id
        )
    ]


@app.post("/api/documents", status_code=201)
def upload_document(
    file: UploadFile = File(...),
    workspace_id: s.Id | None = Form(None),
    project_id: s.Id | None = Form(None),
    actor: Actor = Depends(current_actor),
):
    validate_audience(actor, workspace_id, project_id, write=True)
    data = file.file.read(get_settings().max_upload_bytes + 1)
    filename = Path((file.filename or "document.txt").replace("\\", "/")).name[:240]
    from .services.ingestion import IngestionService

    doc = IngestionService(actor.db, get_settings()).ingest(
        actor.user, data, filename, workspace_id=workspace_id, project_id=project_id
    )
    audit(
        actor,
        "document_uploaded",
        "document",
        doc.id,
        {"workspace_id": workspace_id, "bytes": len(data)},
    )
    actor.db.commit()
    return row(doc, ("body",))


@app.get("/api/documents/{rid}")
def document(rid: s.Id, actor: Actor = Depends(current_actor)):
    return row(access(actor).document(actor.user, rid))


@app.delete("/api/documents/{rid}")
def delete_document(rid: s.Id, actor: Actor = Depends(current_actor)):
    doc = access(actor).document(actor.user, rid, roles=["member", "manager"])
    actor.db.delete(doc)
    audit(actor, "document_deleted", "document", rid)
    result = commit(actor)
    if get_settings().vector_backend == "qdrant":
        from .services.vector_index import QdrantIndex

        QdrantIndex().delete_document(rid)
    return result


@app.get("/api/conversations")
def conversations(
    workspace_id: s.Id | None = None,
    project_id: s.Id | None = None,
    actor: Actor = Depends(current_actor),
):
    validate_audience(actor, workspace_id, project_id)
    return [
        row(c)
        for c in WorkspaceRepository(actor.db).conversations(
            actor.user, workspace_id=workspace_id, project_id=project_id
        )
    ]


@app.post("/api/conversations", status_code=201)
def create_conversation(data: s.ConversationCreate, actor: Actor = Depends(current_actor)):
    validate_audience(actor, data.workspace_id, data.project_id, write=True)
    obj = WorkspaceRepository(actor.db).create_conversation(actor.user, **data.model_dump())
    return commit(actor, obj)


@app.get("/api/conversations/{rid}/messages")
def messages(rid: s.Id, actor: Actor = Depends(current_actor)):
    return [row(msg) for msg in WorkspaceRepository(actor.db).messages(actor.user, rid)]


def chat_allowed(actor, conversation):
    settings = get_settings()
    if not settings.allow_external_ai or not settings.openrouter_api_key:
        raise HTTPException(403, "AI assistance is not enabled for this installation")
    if conversation.workspace_id:
        w = access(actor).workspace(
            actor.user, conversation.workspace_id, roles=["member", "manager"]
        )
        if not w.external_ai_enabled:
            raise HTTPException(403, "External AI is disabled for this workspace")


@app.post("/api/conversations/{rid}/messages")
def send_message(rid: s.Id, data: s.ChatSend, actor: Actor = Depends(current_actor)):
    if not data.external_ai_consent:
        raise HTTPException(
            403, "Confirm that the assistant may use this conversation and its source context"
        )
    conversation = access(actor).conversation(actor.user, rid, roles=["member", "manager"])
    chat_allowed(actor, conversation)
    # Compatibility endpoint executes the same durable graph used by the queued UI.
    from .services.agent_runs import AgentRunService
    from .agents.runtime import AgentRunner
    from .db import SessionLocal

    run = AgentRunService(actor.db).enqueue_chat(
        actor.user, rid, data.content, data.model, str(uuid4())
    )
    run_id = run.id
    actor.db.commit()
    runner = AgentRunner(SessionLocal, get_settings(), OpenRouterGateway())
    lease = runner.claim_one(run_id=run_id)
    if lease:
        asyncio.run(runner.execute(lease))
    actor.db.expire_all()
    from .models_agent import AgentRun

    saved = actor.db.get(AgentRun, run_id)
    if saved.status in {"queued", "running"}:
        from fastapi.encoders import jsonable_encoder

        return JSONResponse(
            status_code=202,
            content=jsonable_encoder(AgentRunService(actor.db).serialize(actor.user, saved)),
        )
    if saved.status != "succeeded":
        raise HTTPException(
            502,
            saved.error or "The agent could not complete this request. See activity for details.",
        )
    access(actor).conversation(actor.user, rid, roles=["member", "manager"])
    user_message = actor.db.get(m.Message, saved.user_message_id)
    assistant_message = actor.db.get(m.Message, saved.assistant_message_id)
    return {
        "user_message": row(user_message),
        "assistant_message": row(assistant_message),
        "run_id": run_id,
    }


# Operational routes use the same session and policy repositories.
from .api_operations import router as operations_router, notifications

app.include_router(operations_router)
from .api_agents import router as agents_router
from .api_knowledge import router as knowledge_router
from .api_personal import router as personal_router
from .api_connections import router as connections_router
from .api_meetings import router as meetings_router
from .api_governance import router as governance_router
from .api_job_drafts import router as job_drafts_router

for feature_router in (
    agents_router,
    knowledge_router,
    personal_router,
    connections_router,
    meetings_router,
    governance_router,
    job_drafts_router,
):
    app.include_router(feature_router)

frontend_dist = Path(get_settings().frontend_dist)
if (frontend_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path == "api" or path.startswith("api/") or path.startswith("assets/"):
        raise HTTPException(404, "Route not found")
    index = frontend_dist / "index.html"
    if not index.is_file():
        raise HTTPException(503, "Build the frontend before starting the application")
    # Always revalidate the shell so a new build is picked up; hashed assets can be cached.
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
