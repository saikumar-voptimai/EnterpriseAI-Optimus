"""Central policy for setup delegation and approved unattended execution.

Approval binds an immutable canonical snapshot. Schedule advancement is execution
state; it is intentionally separate from the approved schedule anchor.
"""

import hashlib
import json
import secrets
from datetime import timedelta
from sqlalchemy import select, or_
from app.models import (
    User,
    Workspace,
    Membership,
    Scope,
    ScheduledJob,
    JobDependency,
    AuditEvent,
    utcnow,
)
from app.models_governance import ScopeRoleAssignment, WorkspaceAutomation, JobRevision
from app.repositories import AccessRepository
from app.services.errors import ServiceError


class GovernanceService:
    def __init__(self, db):
        self.db = db
        self.access = AccessRepository(db)

    def delegated_scopes(self, user, capability="manage_workspaces"):
        if capability not in {"manage_workspaces", "manage_people"}:
            raise ValueError("Unknown capability")
        now = utcnow()
        return list(
            self.db.scalars(
                select(ScopeRoleAssignment.scope_id).where(
                    ScopeRoleAssignment.user_id == user.id,
                    getattr(ScopeRoleAssignment, capability).is_(True),
                    ScopeRoleAssignment.effective_from <= now,
                    or_(
                        ScopeRoleAssignment.effective_until.is_(None),
                        ScopeRoleAssignment.effective_until > now,
                    ),
                )
            )
        )

    def require_scope(self, user, scope_id, capability="manage_workspaces"):
        self.access.require_active(user)
        if not self.db.get(Scope, scope_id):
            raise ServiceError("Scope not found", 404)
        if user.is_admin:
            return
        # Delegation is exact scope, never an implicit grant over descendants.
        if scope_id not in self.delegated_scopes(user, capability):
            raise ServiceError(
                "This organizational scope has not delegated that capability to you.", 403
            )

    def require_people_management(self, user, workspace):
        membership = self.db.get(Membership, (workspace.id, user.id))
        if (
            user.active
            and membership
            and membership.role == "manager"
            and user.clearance >= workspace.classification
        ):
            return
        self.require_scope(user, workspace.scope_id, "manage_people")
        if user.clearance < workspace.classification:
            raise ServiceError("Workspace classification exceeds your clearance.", 403)

    def snapshot(self, job):
        edges = sorted(
            self.db.scalars(
                select(JobDependency.depends_on_id).where(JobDependency.job_id == job.id)
            )
        )
        return {
            key: getattr(job, key)
            for key in (
                "owner_id",
                "workspace_id",
                "name",
                "instructions",
                "model",
                "interval_minutes",
                "external_ai_enabled",
                "task_type",
                "config",
                "timezone",
                "dependency_policy",
            )
        } | {"dependency_ids": edges}

    @staticmethod
    def digest(snapshot):
        return hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def submit(self, user, job):
        self.access.job(user, job.id, roles={"member", "manager"})
        if job.owner_id != user.id:
            self.access.workspace(user, job.workspace_id, roles={"manager"})
        if not job.workspace_id:
            raise ServiceError("Personal jobs do not require a manager approval.")
        prior = self.db.scalar(
            select(JobRevision).where(
                JobRevision.job_id == job.id, JobRevision.revision == job.revision
            )
        )
        snap = self.snapshot(job)
        if prior and prior.digest == self.digest(snap) and prior.decision == "pending":
            return prior
        if prior:
            job.revision += 1
        job.approval_status = "pending"
        job.status = "paused"
        job.approved_revision = None
        revision = JobRevision(
            job_id=job.id,
            revision=job.revision,
            snapshot=snap,
            digest=self.digest(snap),
            submitted_by=user.id,
        )
        self.db.add(revision)
        self.db.flush()
        self.db.add(
            AuditEvent(
                actor_id=user.id,
                action="job_submitted",
                resource_type="job",
                resource_id=job.id,
                details={"revision": job.revision},
            )
        )
        return revision

    def service_principal(self, workspace):
        # Caller locks the workspace, serializing identity creation across approvals.
        automation = self.db.scalar(
            select(WorkspaceAutomation).where(WorkspaceAutomation.workspace_id == workspace.id)
        )
        if automation:
            principal = self.db.get(User, automation.service_user_id)
            if not principal.active:
                raise ServiceError("The workspace automation identity has been disabled.", 409)
            return principal
        principal = User(
            email=f"automation-{workspace.id}@service.invalid",
            name=f"{workspace.name} automation",
            password_hash="!noninteractive:" + secrets.token_hex(32),
            is_service=True,
            is_admin=False,
            active=True,
            clearance=workspace.classification,
        )
        self.db.add(principal)
        self.db.flush()
        self.db.add_all(
            [
                Membership(workspace_id=workspace.id, user_id=principal.id, role="member"),
                WorkspaceAutomation(workspace_id=workspace.id, service_user_id=principal.id),
            ]
        )
        self.db.flush()
        return principal

    def review(self, user, job, *, approve, comment=""):
        self.access.workspace(user, job.workspace_id, roles={"manager"})
        workspace = self.db.scalar(
            select(Workspace).where(Workspace.id == job.workspace_id).with_for_update()
        )
        revision = self.db.scalar(
            select(JobRevision)
            .where(JobRevision.job_id == job.id, JobRevision.revision == job.revision)
            .with_for_update()
        )
        if not revision or revision.decision != "pending" or job.approval_status != "pending":
            raise ServiceError("Submit this job revision for review first.", 409)
        if revision.digest != self.digest(self.snapshot(job)):
            raise ServiceError("The job changed after submission. Submit the new revision.", 409)
        revision.decision = "approved" if approve else "rejected"
        revision.reviewed_by = user.id
        revision.reviewed_at = utcnow()
        revision.comment = comment
        job.approval_status = revision.decision
        if approve:
            principal = self.service_principal(workspace)
            job.execution_user_id = principal.id
            job.approved_revision = job.revision
            job.activation_not_before = utcnow() + timedelta(seconds=20)
            job.status = "active"
        else:
            job.status = "paused"
            job.approved_revision = None
        self.db.add(
            AuditEvent(
                actor_id=user.id,
                action="job_" + revision.decision,
                resource_type="job",
                resource_id=job.id,
                details={"revision": job.revision, "comment": comment},
            )
        )
        return revision

    def require_approved(self, job):
        if job.approval_status == "legacy":
            return  # Existing owner-bound schedules retain their pre-migration policy.
        if job.approval_status != "approved" or job.approved_revision != job.revision:
            raise ServiceError("The current job revision has not been approved.", 409)
        if job.activation_not_before and job.activation_not_before > utcnow():
            raise ServiceError("The approval undo period is still active.", 409)
        if not job.workspace_id:
            return
        revision = self.db.scalar(
            select(JobRevision).where(
                JobRevision.job_id == job.id, JobRevision.revision == job.revision
            )
        )
        if (
            not revision
            or revision.decision != "approved"
            or revision.digest != self.digest(self.snapshot(job))
        ):
            raise ServiceError(
                "Approved instructions or configuration changed. Manager review is required.", 409
            )
        people = {
            person.id: person
            for person in self.db.scalars(
                select(User)
                .where(User.id.in_([job.owner_id, revision.reviewed_by]))
                .order_by(User.id)
                .with_for_update()
            )
        }
        owner = people.get(job.owner_id)
        if not owner or not owner.active:
            raise ServiceError("The accountable employee is inactive.", 403)
        self.db.scalar(
            select(Membership)
            .where(Membership.workspace_id == job.workspace_id, Membership.user_id == owner.id)
            .with_for_update()
        )
        self.access.workspace(owner, job.workspace_id, roles={"member", "manager"})
        reviewer = people.get(revision.reviewed_by)
        if not reviewer or not reviewer.active:
            raise ServiceError(
                "The approving manager is inactive. Review this schedule again.", 403
            )
        self.db.scalar(
            select(Membership)
            .where(Membership.workspace_id == job.workspace_id, Membership.user_id == reviewer.id)
            .with_for_update()
        )
        self.access.workspace(reviewer, job.workspace_id, roles={"manager"})
