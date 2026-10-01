"""Approval, execution identity and revocation are enforced below HTTP/UI."""

from datetime import timedelta
import pytest
from sqlalchemy import select
from app.auth import hash_password, utcnow
from app.config import Settings
from app.models import User, Scope, Workspace, Membership, ScheduledJob, JobRun
from app.models_governance import ScopeRoleAssignment, JobRevision
from app.services.governance import GovernanceService
from app.services.jobs import JobService, require_execution_authorized
from app.services.errors import ServiceError
from app.repositories import AuthorizationError, NotFound
from app.worker import Worker


def seed(db):
    manager = User(
        email="manager@example.com",
        name="Manager",
        password_hash=hash_password("a-long-test-password"),
        clearance=3,
    )
    member = User(
        email="member@example.com",
        name="Engineer",
        password_hash=hash_password("a-long-test-password"),
        clearance=3,
    )
    db.add_all([manager, member])
    db.flush()
    scope = Scope(name="Operations", kind="unit")
    db.add(scope)
    db.flush()
    workspace = Workspace(
        name="Unit workspace", scope_id=scope.id, created_by=manager.id, external_ai_enabled=True
    )
    db.add(workspace)
    db.flush()
    db.add_all(
        [
            Membership(user_id=manager.id, workspace_id=workspace.id, role="manager"),
            Membership(user_id=member.id, workspace_id=workspace.id, role="member"),
        ]
    )
    db.flush()
    settings = Settings(
        allow_external_ai=True,
        openrouter_models="test/model",
        openrouter_default_model="test/model",
        openrouter_api_key="test",
    )
    return manager, member, workspace, settings


def create(db, member, workspace, settings):
    return JobService(db, settings).create(
        member,
        name="Check data",
        instructions="Validate readings",
        workspace_id=workspace.id,
        task_type="validation",
        interval_minutes=15,
        next_run_at=utcnow() - timedelta(minutes=1),
    )


def test_approval_binds_snapshot_undo_and_execution_identity(db, db_factory):
    manager, member, workspace, settings = seed(db)
    job = create(db, member, workspace, settings)
    assert job.status == "paused" and job.approval_status == "pending"
    with pytest.raises(ServiceError, match="not been approved"):
        require_execution_authorized(db, member, job, settings)
    with pytest.raises(AuthorizationError):
        GovernanceService(db).review(member, job, approve=True)
    GovernanceService(db).review(manager, job, approve=True)
    principal = db.get(User, job.execution_user_id)
    assert principal.is_service and not principal.is_admin and principal.id != manager.id
    with pytest.raises(ServiceError, match="undo period"):
        require_execution_authorized(db, principal, job, settings)
    db.commit()
    worker = Worker(db_factory, settings)
    assert worker.schedule_due() == 0
    job.activation_not_before = utcnow() - timedelta(seconds=1)
    db.commit()
    require_execution_authorized(db, principal, job, settings)
    db.rollback()
    assert worker.schedule_due() == 1
    db.expire_all()
    job.config = {"changed": True}
    db.flush()
    with pytest.raises(ServiceError, match="configuration changed"):
        require_execution_authorized(db, principal, job, settings)


def test_manager_or_accountable_member_revocation_stops_service_principal(db):
    manager, member, workspace, settings = seed(db)
    job = create(db, member, workspace, settings)
    GovernanceService(db).review(manager, job, approve=True)
    job.activation_not_before = utcnow() - timedelta(seconds=1)
    db.flush()
    principal = db.get(User, job.execution_user_id)
    require_execution_authorized(db, principal, job, settings)
    db.delete(db.get(Membership, (workspace.id, member.id)))
    db.flush()
    with pytest.raises(NotFound):
        require_execution_authorized(db, principal, job, settings)


def test_scope_delegation_is_exact_and_expires(db):
    manager, member, workspace, settings = seed(db)
    child = Scope(name="Child", kind="team", parent_id=workspace.scope_id)
    db.add(child)
    db.flush()
    assignment = ScopeRoleAssignment(
        user_id=member.id, scope_id=workspace.scope_id, assigned_by=manager.id
    )
    db.add(assignment)
    db.flush()
    service = GovernanceService(db)
    service.require_scope(member, workspace.scope_id)
    with pytest.raises(ServiceError):
        service.require_scope(member, child.id)
    assignment.effective_until = utcnow() - timedelta(seconds=1)
    db.flush()
    with pytest.raises(ServiceError):
        service.require_scope(member, workspace.scope_id)


def test_service_principal_cannot_sign_in(client, db):
    manager, member, workspace, settings = seed(db)
    job = create(db, member, workspace, settings)
    GovernanceService(db).review(manager, job, approve=True)
    principal = db.get(User, job.execution_user_id)
    principal.password_hash = hash_password("a-long-test-password")
    db.commit()
    response = client.post(
        "/api/auth/login", json={"email": principal.email, "password": "a-long-test-password"}
    )
    assert response.status_code == 401


def approved_run(db, manager, member, workspace, settings):
    job = create(db, member, workspace, settings)
    GovernanceService(db).review(manager, job, approve=True)
    job.activation_not_before = utcnow() - timedelta(seconds=1)
    db.flush()
    run = JobService(db, settings).enqueue(member, job)
    db.commit()
    return job, run


def test_workspace_failure_notifies_accountable_employee_not_service_identity(db, db_factory):
    from app.models import Notification

    manager, member, workspace, settings = seed(db)
    job, run = approved_run(db, manager, member, workspace, settings)
    member_id, principal_id = member.id, job.execution_user_id
    worker = Worker(db_factory, settings)
    lease = worker.claim_one()
    worker._record_failure(lease, ServiceError("Configured source unavailable.", 500))
    db.expire_all()
    notifications = list(db.scalars(select(Notification).where(Notification.resource_id == job.id)))
    assert any(n.user_id == member_id and n.kind == "job_failed" for n in notifications)
    assert not any(n.user_id == principal_id for n in notifications)


def test_accountability_revocation_pauses_schedule_and_notifies_managers(db, db_factory):
    from app.models import Notification

    manager, member, workspace, settings = seed(db)
    job, run = approved_run(db, manager, member, workspace, settings)
    job_id, manager_id, workspace_id, member_id = job.id, manager.id, workspace.id, member.id
    worker = Worker(db_factory, settings)
    lease = worker.claim_one()
    db.delete(db.get(Membership, (workspace_id, member_id)))
    db.commit()
    worker._record_failure(lease, ServiceError("Accountability changed.", 409))
    db.expire_all()
    assert db.get(ScheduledJob, job_id).status == "paused"
    assert db.get(JobRun, run.id).status == "blocked"
    assert (
        db.scalar(
            select(Notification).where(
                Notification.resource_id == job_id,
                Notification.user_id == manager_id,
                Notification.kind == "job_governance",
            )
        )
        is not None
    )


def test_dependency_report_from_old_definition_is_rejected_after_reapproval(db, db_factory):
    from app.models import Report

    manager, member, workspace, settings = seed(db)
    schedule = utcnow() + timedelta(hours=1)
    service = JobService(db, settings)
    dependency = service.create(
        member,
        name="Validation",
        instructions="Old approved limits",
        workspace_id=workspace.id,
        task_type="validation",
        interval_minutes=15,
        next_run_at=schedule,
    )
    parent = service.create(
        member,
        name="Dependent handover",
        instructions="Use current validation",
        workspace_id=workspace.id,
        task_type="handover",
        interval_minutes=480,
        next_run_at=schedule,
        dependency_ids=[dependency.id],
    )
    for job in [dependency, parent]:
        GovernanceService(db).review(manager, job, approve=True)
        job.activation_not_before = utcnow() - timedelta(seconds=1)
    db.flush()
    dependency_run = JobService.insert_occurrence(db, dependency.id, schedule)
    dependency_run.status = "succeeded"
    dependency_run.outcome = {"outcome": "pass", "job_revision": dependency.revision}
    db.add(
        Report(
            workspace_id=workspace.id,
            job_run_id=dependency_run.id,
            title="Validation passed",
            body="Old limits passed",
            source_refs=[],
        )
    )
    parent_run = JobService.insert_occurrence(db, parent.id, schedule)
    db.flush()
    worker = Worker(db_factory, settings)
    assert worker._dependencies(db, parent, parent_run, schedule)[0] == "ready"
    dependency.instructions = "Changed limits"
    GovernanceService(db).submit(member, dependency)
    GovernanceService(db).review(manager, dependency, approve=True)
    dependency.activation_not_before = utcnow() - timedelta(seconds=1)
    db.flush()
    state, reason, _ = worker._dependencies(db, parent, parent_run, schedule)
    assert state == "blocked" and "previous job revision" in reason


def test_scheduled_temporal_join_requires_exact_fresh_validation_not_older_success(db, db_factory):
    from datetime import datetime, timezone
    from app.models import Report

    manager, member, workspace, settings = seed(db)
    anchor = datetime(2026, 9, 1, 0, 2, tzinfo=timezone.utc)
    stop = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
    required = stop - timedelta(minutes=13)
    service = JobService(db, settings)
    dependency = service.create(
        member,
        name="Quarter-hour checks",
        instructions="Validate",
        workspace_id=workspace.id,
        task_type="validation",
        interval_minutes=15,
        next_run_at=anchor,
    )
    parent = service.create(
        member,
        name="Eight-hour handover",
        instructions="Handover",
        workspace_id=workspace.id,
        task_type="handover",
        interval_minutes=480,
        next_run_at=stop - timedelta(hours=8),
        dependency_ids=[dependency.id],
    )
    for job in [dependency, parent]:
        GovernanceService(db).review(manager, job, approve=True)
        job.activation_not_before = utcnow() - timedelta(seconds=1)
    old_run = JobService.insert_occurrence(db, dependency.id, required - timedelta(minutes=15))
    old_run.status = "succeeded"
    old_run.outcome = {"outcome": "pass", "job_revision": dependency.revision}
    db.add(
        Report(
            workspace_id=workspace.id,
            job_run_id=old_run.id,
            title="Older success",
            body="Must not substitute this report",
            source_refs=[],
        )
    )
    parent_run = JobService.insert_occurrence(db, parent.id, stop)
    db.flush()
    worker = Worker(db_factory, settings)
    assert worker._dependencies(db, parent, parent_run, stop)[0] == "waiting"
    dependency.next_run_at = required + timedelta(minutes=15)
    db.flush()
    assert worker._dependencies(db, parent, parent_run, stop)[0] == "blocked"
    expected_run = JobService.insert_occurrence(db, dependency.id, required)
    expected_run.status = "failed"
    db.flush()
    assert worker._dependencies(db, parent, parent_run, stop)[0] == "blocked"
    expected_run.status = "succeeded"
    expected_run.outcome = {"outcome": "pass", "job_revision": dependency.revision}
    db.add(
        Report(
            workspace_id=workspace.id,
            job_run_id=expected_run.id,
            title="Expected validation",
            body="Fresh required result",
            source_refs=[],
        )
    )
    db.flush()
    state, _, results = worker._dependencies(db, parent, parent_run, stop)
    assert state == "ready" and len(results) == 1 and results[0]["body"] == "Fresh required result"
