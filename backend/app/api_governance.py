"""First-run initialization and explicit organizational delegations."""

import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import select, text
from app import models as m, schemas as s
from app.auth import (
    Actor,
    admin_actor,
    current_actor,
    get_session,
    require_origin,
    rate_limit_login,
    hash_password,
    new_session,
    COOKIE_NAME,
)
from app.config import get_settings
from app.models_governance import ScopeRoleAssignment, JobRevision
from app.services.governance import GovernanceService
from app.services.errors import ServiceError

router = APIRouter()


class Setup(s.Input):
    token: str = Field(min_length=20, max_length=256)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=256)
    enterprise_name: str = Field(default="My enterprise", min_length=1, max_length=160)


@router.get("/api/setup/status")
def setup_status(db=Depends(get_session)):
    return {"required": db.scalar(select(m.User.id).limit(1)) is None, "version": "2.0.0"}


@router.post("/api/setup", status_code=201)
def setup(data: Setup, request: Request, response: Response, db=Depends(get_session)):
    require_origin(request)
    settings = get_settings()
    rate_limit_login(db, "initial-setup", request.client.host if request.client else "unknown")
    if not settings.bootstrap_token or not secrets.compare_digest(
        data.token, settings.bootstrap_token
    ):
        raise HTTPException(403, "Enter the setup token printed by scripts/start.sh.")
    db.execute(text("SELECT pg_advisory_xact_lock(893276422)"))
    if db.scalar(select(m.User.id).limit(1)):
        raise HTTPException(409, "Setup has already been completed. Sign in.")
    user = m.User(
        email=data.email.lower(),
        name=data.name,
        password_hash=hash_password(data.password),
        is_admin=True,
        clearance=3,
    )
    db.add(user)
    db.flush()
    scope = m.Scope(name=data.enterprise_name, kind="enterprise")
    db.add(scope)
    db.flush()
    workspace = m.Workspace(
        name="Enterprise coordination",
        description="Shared priorities, decisions and published operational summaries.",
        scope_id=scope.id,
        created_by=user.id,
        external_ai_enabled=settings.allow_external_ai,
    )
    db.add(workspace)
    db.flush()
    db.add_all(
        [
            m.Membership(workspace_id=workspace.id, user_id=user.id, role="manager"),
            m.ScopeGrant(user_id=user.id, scope_id=scope.id, can_review=True),
            m.AppSetting(key="hierarchy_labels", value=["enterprise", "site", "unit", "team"]),
            m.AuditEvent(
                actor_id=user.id,
                action="installation_initialized",
                resource_type="workspace",
                resource_id=workspace.id,
                details={},
            ),
        ]
    )
    auth_session, token = new_session(db, user)
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return {
        "user": {
            key: getattr(user, key)
            for key in ("id", "email", "name", "is_admin", "active", "clearance")
        },
        "csrf_token": auth_session.csrf_token,
    }


class Delegation(s.Input):
    user_id: s.Id
    scope_id: s.Id
    manage_workspaces: bool = True
    manage_people: bool = False
    effective_until: datetime | None = None

    @field_validator("effective_until")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return value


@router.get("/api/admin/delegations")
def delegations(actor: Actor = Depends(admin_actor)):
    from app.api_operations import row

    return [
        row(r)
        for r in actor.db.scalars(
            select(ScopeRoleAssignment).order_by(ScopeRoleAssignment.created_at)
        )
    ]


@router.post("/api/admin/delegations", status_code=201)
def delegate(data: Delegation, actor: Actor = Depends(admin_actor)):
    GovernanceService(actor.db).require_scope(actor.user, data.scope_id)
    user = actor.db.get(m.User, data.user_id)
    if not user or not user.active or user.is_service:
        raise HTTPException(404, "Employee not found")
    obj = actor.db.scalar(
        select(ScopeRoleAssignment)
        .where(
            ScopeRoleAssignment.user_id == data.user_id,
            ScopeRoleAssignment.scope_id == data.scope_id,
        )
        .with_for_update()
    )
    if obj:
        for k, v in data.model_dump().items():
            setattr(obj, k, v)
        obj.assigned_by = actor.user.id
    else:
        obj = ScopeRoleAssignment(**data.model_dump(), assigned_by=actor.user.id)
        actor.db.add(obj)
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action="scope_delegation_updated",
            resource_type="scope",
            resource_id=data.scope_id,
            details={"user_id": user.id},
        )
    )
    actor.db.commit()
    from app.api_operations import row

    return row(obj)


@router.delete("/api/admin/delegations/{rid}")
def revoke(rid: s.Id, actor: Actor = Depends(admin_actor)):
    obj = actor.db.get(ScopeRoleAssignment, rid)
    if not obj:
        raise HTTPException(404, "Delegation not found")
    actor.db.delete(obj)
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action="scope_delegation_revoked",
            resource_type="scope",
            resource_id=obj.scope_id,
            details={"user_id": obj.user_id},
        )
    )
    actor.db.commit()
    return {"ok": True}


class Review(s.Input):
    comment: str = Field(default="", max_length=2000)


def locked_job(actor, rid):
    from app.repositories import AccessRepository

    AccessRepository(actor.db).job(actor.user, rid)
    return actor.db.scalar(select(m.ScheduledJob).where(m.ScheduledJob.id == rid).with_for_update())


@router.post("/api/jobs/{rid}/submit")
def submit(rid: s.Id, actor: Actor = Depends(current_actor)):
    job = locked_job(actor, rid)
    GovernanceService(actor.db).submit(actor.user, job)
    actor.db.commit()
    from app.api_operations import job_payload

    return job_payload(actor, job)


@router.post("/api/jobs/{rid}/approve")
def approve(rid: s.Id, data: Review, actor: Actor = Depends(current_actor)):
    job = locked_job(actor, rid)
    GovernanceService(actor.db).review(actor.user, job, approve=True, comment=data.comment)
    actor.db.commit()
    from app.api_operations import job_payload

    return job_payload(actor, job)


@router.post("/api/jobs/{rid}/reject")
def reject(rid: s.Id, data: Review, actor: Actor = Depends(current_actor)):
    job = locked_job(actor, rid)
    GovernanceService(actor.db).review(actor.user, job, approve=False, comment=data.comment)
    actor.db.commit()
    from app.api_operations import job_payload

    return job_payload(actor, job)


@router.post("/api/jobs/{rid}/undo-approval")
def undo_approval(rid: s.Id, actor: Actor = Depends(current_actor)):
    job = locked_job(actor, rid)
    from app.repositories import AccessRepository

    AccessRepository(actor.db).workspace(actor.user, job.workspace_id, roles={"manager"})
    if not job.activation_not_before or job.activation_not_before <= m.utcnow():
        raise HTTPException(409, "The undo period has ended. Pause the job instead.")
    job.status = "paused"
    job.approval_status = "pending"
    job.approved_revision = None
    revision = actor.db.scalar(
        select(JobRevision).where(
            JobRevision.job_id == job.id, JobRevision.revision == job.revision
        )
    )
    revision.decision = "pending"
    revision.reviewed_at = None
    revision.reviewed_by = None
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action="job_approval_undone",
            resource_type="job",
            resource_id=job.id,
            details={"revision": job.revision},
        )
    )
    actor.db.commit()
    from app.api_operations import job_payload

    return job_payload(actor, job)


JOB_TEMPLATES = [
    {
        "id": "validation",
        "name": "15-minute data validation",
        "task_type": "validation",
        "interval_minutes": 15,
        "instructions": "Validate configured signals for missing, stale and out-of-range values.",
        "config": {"metrics": []},
    },
    {
        "id": "handover",
        "name": "8-hour shift handover",
        "task_type": "handover",
        "interval_minutes": 480,
        "instructions": "Summarize the completed shift, incidents and outstanding actions.",
        "config": {"metrics": [], "window_hours": 8},
    },
    {
        "id": "morning_brief",
        "name": "Morning brief",
        "task_type": "morning_brief",
        "interval_minutes": 1440,
        "instructions": "Brief me on authorized operational summaries, open incidents, reminders and upcoming meetings. Separate facts from follow-ups.",
        "config": {},
    },
    {
        "id": "production_comparison",
        "name": "Weekly production comparison",
        "task_type": "production_comparison",
        "interval_minutes": 10080,
        "instructions": "Compare the completed week against the preceding week, two weeks ago and previous calendar month, with rates normalized for window duration.",
        "config": {"metrics": []},
    },
]


@router.get("/api/jobs/templates")
def templates(actor: Actor = Depends(current_actor)):
    return JOB_TEMPLATES


class HierarchyLabels(s.Input):
    labels: list[str] = Field(min_length=1, max_length=12)

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, values):
        if any(not value.strip() or len(value) > 60 for value in values):
            raise ValueError("Each hierarchy label must contain 1–60 characters")
        if len(set(values)) != len(values):
            raise ValueError("Hierarchy labels must be distinct")
        return [value.strip() for value in values]


@router.get("/api/hierarchy-labels")
def hierarchy_labels(actor: Actor = Depends(current_actor)):
    setting = actor.db.scalar(select(m.AppSetting).where(m.AppSetting.key == "hierarchy_labels"))
    return {"labels": setting.value if setting else ["enterprise", "site", "unit", "team"]}


@router.put("/api/admin/hierarchy-labels")
def update_hierarchy_labels(data: HierarchyLabels, actor: Actor = Depends(admin_actor)):
    setting = actor.db.scalar(
        select(m.AppSetting).where(m.AppSetting.key == "hierarchy_labels").with_for_update()
    )
    if setting:
        setting.value = data.labels
    else:
        actor.db.add(m.AppSetting(key="hierarchy_labels", value=data.labels))
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action="hierarchy_labels_updated",
            resource_type="settings",
            details={"labels": data.labels},
        )
    )
    actor.db.commit()
    return {"labels": data.labels}


class JobEdit(s.Input):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    instructions: str | None = Field(default=None, min_length=1, max_length=12000)
    model: str | None = Field(default=None, max_length=200)
    config: dict | None = None
    external_ai_enabled: bool | None = None
    dependency_ids: list[s.Id] | None = Field(default=None, max_length=20)


@router.put("/api/jobs/{rid}/definition")
def edit_job(rid: s.Id, data: JobEdit, actor: Actor = Depends(current_actor)):
    from app.repositories import AccessRepository
    from app.services.gateway import select_model
    from app.services.jobs import JobService, validate_job_config

    job = locked_job(actor, rid)
    if job.owner_id != actor.user.id:
        if not job.workspace_id:
            raise HTTPException(403, "Only the owner can edit a personal job")
        AccessRepository(actor.db).workspace(actor.user, job.workspace_id, roles={"manager"})
    changes = data.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(422, "Job definition fields cannot be null")
    if "model" in changes:
        changes["model"] = select_model(changes["model"])
    if "config" in changes:
        changes["config"]["schedule_anchor"] = job.config.get(
            "schedule_anchor", job.next_run_at.isoformat()
        )
        validate_job_config(
            actor.db, actor.user, job.task_type, changes["config"], job.workspace_id
        )
    if changes.get("external_ai_enabled") and job.workspace_id:
        workspace = AccessRepository(actor.db).workspace(
            actor.user, job.workspace_id, roles={"member", "manager"}
        )
        if not workspace.external_ai_enabled:
            raise HTTPException(403, "External AI is disabled for this workspace")
    dependencies = changes.pop("dependency_ids", None)
    for key, value in changes.items():
        setattr(job, key, value)
    actor.db.flush()
    if dependencies is not None:
        # Managers can review but dependency topology remains accountable-owner authored.
        JobService(actor.db).update_dependencies(actor.user, job, dependencies)
    if job.workspace_id:
        GovernanceService(actor.db).submit(actor.user, job)
    else:
        job.revision += 1
        job.approved_revision = job.revision
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action="job_definition_updated",
            resource_type="job",
            resource_id=job.id,
            details={"revision": job.revision, "fields": list(changes)},
        )
    )
    actor.db.commit()
    from app.api_operations import job_payload

    return job_payload(actor, job)
