"""Incident coordination, authorized notifications, scheduled work, and administration."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.dialects.postgresql import insert

from . import models as m, schemas as s
from .config import get_settings
from .auth import Actor, admin_actor, current_actor, hash_password, utcnow
from .repositories import AccessRepository, AuthorizationError, NotFound, WorkspaceRepository
from .services.jobs import JobService

router = APIRouter()


def row(obj, exclude=()):
    hidden = {"password_hash", "token_hash", "csrf_token", "lease_token"} | set(exclude)
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


def access(actor):
    return AccessRepository(actor.db)


def audit(actor, action, resource_type, resource_id, details=None):
    # Only structural metadata belongs in this installation-wide audit trail.
    actor.db.add(
        m.AuditEvent(
            actor_id=actor.user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
    )


def commit(actor, obj=None):
    actor.db.commit()
    return row(obj) if obj is not None else {"ok": True}


from .services.incidents import IncidentService


def notification_payload(actor, item):
    """Reconstruct content from the currently authorized resource, never stale text."""
    policy = access(actor)
    if item.resource_type == "publication":
        target = policy.publication(actor.user, item.resource_id)
        title, body = target["title"], target["summary"]
    elif item.resource_type == "incident":
        target = policy.incident(actor.user, item.resource_id)
        title, body = target.title, target.summary
    elif item.resource_type == "reminder":
        target = policy.reminder(actor.user, item.resource_id)
        title, body = "Reminder due", target.title
    elif item.resource_type == "report":
        target = policy.report(actor.user, item.resource_id)
        title, body = "Scheduled report ready", target.title
    elif item.resource_type == "job":
        target = policy.job(actor.user, item.resource_id)
        title, body = "Scheduled task update", target.name
    elif item.resource_type == "job_run":
        target = policy.job_run(actor.user, item.resource_id)
        title, body = "Scheduled task update", target.status
    elif item.resource_type in {"checkin", "organization"}:
        from .models_personal import DailyCheckin, OrganizationChange

        target_class = DailyCheckin if item.resource_type == "checkin" else OrganizationChange
        target = actor.db.get(target_class, item.resource_id)
        if target is None or target.owner_id != actor.user.id:
            raise NotFound("Notification target is no longer available")
        if item.resource_type == "checkin":
            title, body = (
                "Your five-minute check-in",
                "Open Optimus to record what happened and your next steps.",
            )
        else:
            title, body = (
                "Notebook organization",
                f"{len(target.moves)} notes; status: {target.status}.",
            )
    else:
        raise NotFound("Notification target is no longer available")
    return {**row(item, exclude={"dedupe_key"}), "title": title, "body": body}


@router.get("/api/incidents")
def incidents(workspace_id: s.Id | None = None, actor: Actor = Depends(current_actor)):
    return [
        row(item)
        for item in WorkspaceRepository(actor.db).incidents(actor.user, workspace_id=workspace_id)
    ]


@router.post("/api/incidents", status_code=201)
def create_incident(data: s.IncidentCreate, actor: Actor = Depends(current_actor)):
    obj = IncidentService(actor.db).create(actor.user, **data.model_dump())
    return commit(actor, obj)


@router.post("/api/incidents/{rid}/actions")
def incident_action(rid: s.Id, data: s.IncidentAction, actor: Actor = Depends(current_actor)):
    obj = IncidentService(actor.db).transition(actor.user, rid, **data.model_dump())
    return commit(actor, obj)


@router.get("/api/publications")
def publications(actor: Actor = Depends(current_actor)):
    return access(actor).visible_publications(actor.user)


@router.get("/api/subscriptions")
def subscriptions(actor: Actor = Depends(current_actor)):
    permitted = {scope.id for scope in access(actor).reviewable_scopes(actor.user)}
    return [
        row(item)
        for item in actor.db.scalars(
            select(m.Subscription)
            .where(m.Subscription.user_id == actor.user.id, m.Subscription.scope_id.in_(permitted))
            .order_by(m.Subscription.created_at)
        )
    ]


@router.put("/api/subscriptions")
def update_subscriptions(data: s.SubscriptionUpdate, actor: Actor = Depends(current_actor)):
    requested = set(data.scope_ids)
    permitted = {scope.id for scope in access(actor).reviewable_scopes(actor.user)}
    if not requested <= permitted:
        raise HTTPException(403, "You may subscribe only to scopes within your current access")
    actor.db.execute(delete(m.Subscription).where(m.Subscription.user_id == actor.user.id))
    for scope_id in sorted(requested):
        actor.db.add(m.Subscription(user_id=actor.user.id, scope_id=scope_id))
    actor.db.commit()
    return subscriptions(actor)


@router.get("/api/notifications")
def notifications(actor: Actor = Depends(current_actor)):
    result = []
    items = actor.db.scalars(
        select(m.Notification)
        .where(m.Notification.user_id == actor.user.id)
        .order_by(m.Notification.created_at.desc(), m.Notification.id)
        .limit(500)
    )
    for item in items:
        try:
            result.append(notification_payload(actor, item))
        except (NotFound, AuthorizationError):
            continue
        if len(result) >= 200:
            break
    return result


@router.post("/api/notifications/{rid}/read")
def read_notification(rid: s.Id, actor: Actor = Depends(current_actor)):
    obj = access(actor).notification(actor.user, rid)
    notification_payload(actor, obj)
    obj.read_at = obj.read_at or utcnow()
    actor.db.commit()
    # Reading a notification never mutates an incident's acknowledgement state.
    return notification_payload(actor, obj)


def job_payload(actor, job):
    dependencies = list(
        actor.db.scalars(
            select(m.JobDependency.depends_on_id).where(m.JobDependency.job_id == job.id)
        )
    )
    membership = (
        actor.db.get(m.Membership, (job.workspace_id, actor.user.id)) if job.workspace_id else None
    )
    return {
        **row(job),
        "dependency_ids": dependencies,
        "can_approve": bool(membership and membership.role == "manager"),
    }


@router.get("/api/jobs")
def jobs(actor: Actor = Depends(current_actor)):
    return [job_payload(actor, job) for job in WorkspaceRepository(actor.db).jobs(actor.user)]


@router.post("/api/jobs", status_code=201)
def create_job(data: s.JobCreate, actor: Actor = Depends(current_actor)):
    obj = JobService(actor.db).create(actor.user, **data.model_dump())
    audit(actor, "job_created", "job", obj.id)
    actor.db.commit()
    return job_payload(actor, obj)


@router.patch("/api/jobs/{rid}")
def update_job(rid: s.Id, data: s.JobUpdate, actor: Actor = Depends(current_actor)):
    obj = access(actor).job(actor.user, rid, roles={"member", "manager"})
    if obj.owner_id != actor.user.id:
        if not obj.workspace_id:
            raise HTTPException(403, "Only the task owner can configure this task")
        access(actor).workspace(actor.user, obj.workspace_id, roles={"manager"})
    obj = actor.db.scalar(
        select(m.ScheduledJob).where(m.ScheduledJob.id == obj.id).with_for_update()
    )
    if obj.status == "completed" and data.status == "active":
        raise HTTPException(
            409, "This one-time schedule is complete. Use Run now for another execution."
        )
    if data.status == "active":
        from .services.governance import GovernanceService

        GovernanceService(actor.db).require_approved(obj)
    obj.status = data.status
    audit(actor, "job_status_changed", "job", obj.id, {"status": data.status})
    actor.db.commit()
    return job_payload(actor, obj)


@router.post("/api/jobs/{rid}/run", status_code=202)
def run_job(rid: s.Id, actor: Actor = Depends(current_actor)):
    obj = access(actor).job(actor.user, rid, roles={"member", "manager"})
    run = JobService(actor.db).enqueue(actor.user, obj)
    audit(actor, "job_run_queued", "job_run", run.id)
    return commit(actor, run)


@router.get("/api/jobs/{rid}/runs")
def job_runs(rid: s.Id, actor: Actor = Depends(current_actor)):
    return [row(item) for item in WorkspaceRepository(actor.db).job_runs(actor.user, rid)]


@router.get("/api/reports")
def reports(workspace_id: s.Id | None = None, actor: Actor = Depends(current_actor)):
    return [
        row(item)
        for item in WorkspaceRepository(actor.db).reports(actor.user, workspace_id=workspace_id)
    ]


@router.get("/api/admin/users")
def users(actor: Actor = Depends(admin_actor)):
    return [
        public_user(user)
        for user in actor.db.scalars(select(m.User).order_by(m.User.name, m.User.id).limit(1000))
    ]


@router.post("/api/admin/users", status_code=201)
def create_user(data: s.UserCreate, actor: Actor = Depends(admin_actor)):
    email = data.email.strip().lower()
    if actor.db.scalar(select(m.User.id).where(m.User.email == email)):
        raise HTTPException(409, "A user with this email address already exists")
    obj = m.User(
        email=email,
        name=data.name,
        password_hash=hash_password(data.password),
        is_admin=data.is_admin,
        clearance=data.clearance,
        active=True,
    )
    actor.db.add(obj)
    actor.db.flush()
    audit(
        actor,
        "user_created",
        "user",
        obj.id,
        {"is_admin": obj.is_admin, "clearance": obj.clearance},
    )
    actor.db.commit()
    return public_user(obj)


@router.patch("/api/admin/users/{rid}")
def update_user(rid: s.Id, data: s.UserUpdate, actor: Actor = Depends(admin_actor)):
    # Lock all administrators in a stable order so concurrent deactivations cannot
    # each observe the other as the final active administrator.
    admins = list(
        actor.db.scalars(
            select(m.User)
            .where(m.User.is_admin.is_(True))
            .order_by(m.User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    obj = actor.db.scalar(
        select(m.User)
        .where(m.User.id == rid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if obj is None:
        raise HTTPException(404, "User not found")
    changes = data.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(422, "Fields cannot be null")
    if changes.get("active") is False and obj.is_admin and obj.active:
        if sum(user.active for user in admins) <= 1:
            raise HTTPException(409, "Keep at least one active administrator")
    for key, value in changes.items():
        setattr(obj, key, value)
    if changes.get("active") is False:
        actor.db.execute(delete(m.AuthSession).where(m.AuthSession.user_id == obj.id))
    audit(actor, "user_updated", "user", obj.id, changes)
    actor.db.commit()
    return public_user(obj)


@router.get("/api/admin/scopes")
def admin_scopes(actor: Actor = Depends(admin_actor)):
    return [
        row(scope)
        for scope in actor.db.scalars(
            select(m.Scope).order_by(m.Scope.created_at, m.Scope.id).limit(1000)
        )
    ]


@router.post("/api/admin/scopes", status_code=201)
def create_scope(data: s.ScopeCreate, actor: Actor = Depends(admin_actor)):
    if data.parent_id and actor.db.get(m.Scope, data.parent_id) is None:
        raise HTTPException(404, "Parent scope not found")
    obj = m.Scope(**data.model_dump(exclude={"create_default_workspace"}))
    actor.db.add(obj)
    actor.db.flush()
    if data.create_default_workspace:
        workspace = m.Workspace(
            name=obj.name + " coordination",
            description="Shared knowledge, data validation and incident coordination for "
            + obj.name,
            scope_id=obj.id,
            created_by=actor.user.id,
        )
        actor.db.add(workspace)
        actor.db.flush()
        actor.db.add(m.Membership(workspace_id=workspace.id, user_id=actor.user.id, role="manager"))
    audit(actor, "scope_created", "scope", obj.id)
    return commit(actor, obj)


@router.post("/api/admin/scope-grants")
def create_scope_grant(data: s.GrantCreate, actor: Actor = Depends(admin_actor)):
    user = actor.db.get(m.User, data.user_id)
    if user is None or not user.active or actor.db.get(m.Scope, data.scope_id) is None:
        raise HTTPException(404, "Active user or scope not found")
    actor.db.execute(
        insert(m.ScopeGrant)
        .values(user_id=data.user_id, scope_id=data.scope_id, can_review=True)
        .on_conflict_do_update(
            index_elements=[m.ScopeGrant.user_id, m.ScopeGrant.scope_id], set_={"can_review": True}
        )
    )
    audit(actor, "scope_grant_created", "scope", data.scope_id, {"user_id": data.user_id})
    return commit(actor)


@router.get("/api/admin/scope-grants")
def scope_grants(actor: Actor = Depends(admin_actor)):
    return [
        row(grant)
        for grant in actor.db.scalars(
            select(m.ScopeGrant).order_by(m.ScopeGrant.scope_id, m.ScopeGrant.user_id).limit(2000)
        )
    ]


@router.delete("/api/admin/scope-grants/{user_id}/{scope_id}")
def revoke_scope_grant(user_id: s.Id, scope_id: s.Id, actor: Actor = Depends(admin_actor)):
    grant = actor.db.get(m.ScopeGrant, (user_id, scope_id))
    if grant is None:
        raise HTTPException(404, "Scope grant not found")
    actor.db.delete(grant)
    audit(actor, "scope_grant_revoked", "scope", scope_id, {"user_id": user_id})
    return commit(actor)


@router.get("/api/admin/audit")
def admin_audit(actor: Actor = Depends(admin_actor)):
    return [
        row(item)
        for item in actor.db.scalars(
            select(m.AuditEvent)
            .order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id)
            .limit(500)
        )
    ]
