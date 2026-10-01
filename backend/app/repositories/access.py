"""Central access queries. An admin role never substitutes for resource access."""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID
from typing import Any, TypeVar

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session, aliased

from ..models import (
    Conversation,
    Document,
    Incident,
    JobRun,
    Membership,
    Memory,
    Note,
    Notification,
    Project,
    Reminder,
    Report,
    ScheduledJob,
    Scope,
    ScopeGrant,
    User,
    Workspace,
)


class AuthorizationError(Exception):
    """An authenticated principal cannot perform the requested operation."""


class NotFound(Exception):
    """A resource does not exist in this principal's accessible context."""


PrivateModel = TypeVar("PrivateModel", Project, Note, Memory, Reminder)
AudienceModel = TypeVar("AudienceModel", Document, Conversation, Report)


class AccessRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def require_active(user: User) -> None:
        if not user.active:
            raise AuthorizationError("Account is inactive")

    def workspace_ids(self, user: User, roles: Collection[str] | None = None) -> Select:
        self.require_active(user)
        stmt = (
            select(Workspace.id)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id, Workspace.classification <= user.clearance)
        )
        if roles is not None:
            stmt = stmt.where(Membership.role.in_(roles))
        return stmt

    def accessible_workspaces(self, user: User) -> Sequence[Workspace]:
        return self.session.scalars(
            select(Workspace)
            .where(Workspace.id.in_(self.workspace_ids(user)))
            .order_by(Workspace.name, Workspace.id)
        ).all()

    def reviewable_scopes(self, user: User) -> Sequence[Scope]:
        """Scopes available for subscriptions and navigation, never raw data grants.

        Review grants contribute their exact scope and direct children; current
        memberships contribute only the workspace's actual scope. Critical
        escalations are individually authorized by publication_statement().
        """
        self.require_active(user)
        grants = select(ScopeGrant.scope_id).where(
            ScopeGrant.user_id == user.id, ScopeGrant.can_review.is_(True)
        )
        direct = select(Scope.id).where(Scope.parent_id.in_(grants))
        member_scopes = select(Workspace.scope_id).where(Workspace.id.in_(self.workspace_ids(user)))
        return self.session.scalars(
            select(Scope)
            .where(or_(Scope.id.in_(grants), Scope.id.in_(direct), Scope.id.in_(member_scopes)))
            .order_by(Scope.name, Scope.id)
        ).all()

    def workspace(
        self, user: User, workspace_id: str, roles: Collection[str] | None = None
    ) -> Workspace:
        self.require_active(user)
        row = self.session.execute(
            select(Workspace, Membership.role)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Membership.user_id == user.id,
                Workspace.classification <= user.clearance,
            )
        ).first()
        if row is None:
            raise NotFound("Workspace not found")
        workspace, role = row
        if roles is not None and role not in roles:
            raise AuthorizationError("Workspace role does not permit this operation")
        return workspace

    def _private(self, model: type[PrivateModel], user: User, resource_id: str) -> PrivateModel:
        self.require_active(user)
        resource = self.session.scalar(
            select(model).where(model.id == resource_id, model.owner_id == user.id)
        )
        if resource is None:
            raise NotFound("Resource not found")
        return resource

    def project(self, user: User, project_id: str) -> Project:
        return self._private(Project, user, project_id)

    def note(self, user: User, note_id: str) -> Note:
        return self._private(Note, user, note_id)

    def memory(self, user: User, memory_id: str) -> Memory:
        memory = self._private(Memory, user, memory_id)
        if memory.forgotten_at is not None:
            raise NotFound("Memory not found")
        return memory

    def reminder(self, user: User, reminder_id: str) -> Reminder:
        return self._private(Reminder, user, reminder_id)

    def _audience(
        self,
        model: type[AudienceModel],
        user: User,
        resource_id: str,
        roles: Collection[str] | None = None,
    ) -> AudienceModel:
        self.require_active(user)
        resource = self.session.scalar(
            select(model).where(
                model.id == resource_id,
                or_(model.owner_id == user.id, model.workspace_id.in_(self.workspace_ids(user))),
            )
        )
        if resource is None:
            raise NotFound("Resource not found")
        if resource.workspace_id is not None:
            self.workspace(user, resource.workspace_id, roles)
        return resource

    def document(
        self, user: User, document_id: str, roles: Collection[str] | None = None
    ) -> Document:
        return self._audience(Document, user, document_id, roles)

    def conversation(
        self, user: User, conversation_id: str, roles: Collection[str] | None = None
    ) -> Conversation:
        return self._audience(Conversation, user, conversation_id, roles)

    def report(self, user: User, report_id: str) -> Report:
        report = self._audience(Report, user, report_id)
        if not self._refs_authorized(user, report.source_refs, False, {report.id}, {}, [2048]):
            raise NotFound("Report not found")
        return report

    def source_refs_authorized(
        self,
        user: User,
        refs: Any,
        *,
        for_external_ai: bool = False,
        external_ai: bool | None = None,
    ) -> bool:
        """Authorize every original source of derived content, failing closed.

        Cloud replay also requires each source workspace's current egress switch
        and matching recorded version. Report references recurse through their
        own provenance with cycle detection, at most 16 report levels and a
        bounded traversal. This checks source policy, not the installation-wide
        AI switch or per-request consent, which remain service responsibilities.
        """
        if external_ai is not None:
            for_external_ai = for_external_ai or external_ai
        if not user.active:
            return False
        return self._refs_authorized(user, refs, for_external_ai, set(), {}, [2048])

    def source_records_allowed(
        self, user: User, records: Sequence[dict[str, Any]], *, for_external_ai: bool = False
    ) -> list[dict[str, Any]]:
        """Filter source candidate records without exposing a denied source."""
        return [
            record
            for record in records
            if self.source_refs_authorized(user, [record], for_external_ai=for_external_ai)
        ]

    def _refs_authorized(
        self,
        user: User,
        refs: Any,
        for_external_ai: bool,
        path: set[str],
        cache: dict[tuple[str, int], bool],
        budget: list[int],
    ) -> bool:
        if not isinstance(refs, list) or len(refs) > 512:
            return False
        for ref in refs:
            budget[0] -= 1
            if budget[0] < 0 or not isinstance(ref, dict):
                return False
            try:
                identifier = str(UUID(str(ref.get("id", ""))))
                resource_type = ref.get("type")
                if resource_type == "note":
                    source = self.note(user, identifier)
                elif resource_type == "project":
                    source = self.project(user, identifier)
                elif resource_type == "reminder":
                    source = self.reminder(user, identifier)
                elif resource_type == "memory":
                    source = self.memory(user, identifier)
                elif resource_type == "document":
                    source = self.document(user, identifier)
                    if ref.get("revision_id"):
                        from app.models_knowledge import DocumentRevision

                        revision = self.session.get(
                            DocumentRevision, str(UUID(str(ref["revision_id"])))
                        )
                        if (
                            not revision
                            or revision.document_id != source.id
                            or not revision.is_current
                        ):
                            return False
                elif resource_type == "calendar_event":
                    from app.models_connections import CalendarEvent, Connection

                    source = self.session.get(CalendarEvent, identifier)
                    connection = (
                        self.session.get(Connection, source.connection_id) if source else None
                    )
                    if (
                        not source
                        or source.owner_id != user.id
                        or source.cancelled
                        or not connection
                        or connection.status == "disconnected"
                    ):
                        return False
                elif resource_type == "connection":
                    from app.models_connections import Connection

                    source = self.session.get(Connection, identifier)
                    if not source or source.status != "connected":
                        return False
                    if source.workspace_id:
                        self.workspace(user, source.workspace_id)
                    elif source.owner_id != user.id:
                        return False
                elif resource_type == "incident":
                    if ref.get("summary_only") is True:
                        publication = self.publication(user, identifier)
                        source = None
                        workspace_id = publication["workspace_id"]
                        version = publication["updated_at"]
                    else:
                        source = self.incident(user, identifier)
                elif resource_type == "report":
                    source = self._audience(Report, user, identifier)
                    if source.id in path or len(path) >= 16:
                        return False
                    cache_key = (source.id, len(path))
                    if cache_key not in cache:
                        cache[cache_key] = self._refs_authorized(
                            user,
                            source.source_refs,
                            for_external_ai,
                            path | {source.id},
                            cache,
                            budget,
                        )
                    if not cache[cache_key]:
                        return False
                else:
                    return False
                if source is not None:
                    workspace_id = getattr(source, "workspace_id", None)
                    version = source.created_at if isinstance(source, Report) else source.updated_at
                if for_external_ai:
                    if workspace_id is not None:
                        workspace = self.session.scalar(
                            select(Workspace).where(
                                Workspace.id == workspace_id,
                                Workspace.external_ai_enabled.is_(True),
                            )
                        )
                        if workspace is None:
                            return False
                    if "version" in ref:
                        recorded_version = datetime.fromisoformat(
                            str(ref["version"]).replace("Z", "+00:00")
                        )
                        if recorded_version.tzinfo is None or recorded_version != version:
                            return False
            except (NotFound, AuthorizationError, ValueError, TypeError, AttributeError):
                return False
        return True

    def job(self, user: User, job_id: str, roles: Collection[str] | None = None) -> ScheduledJob:
        self.require_active(user)
        job = self.session.scalar(
            select(ScheduledJob).where(
                ScheduledJob.id == job_id,
                or_(
                    and_(ScheduledJob.owner_id == user.id, ScheduledJob.workspace_id.is_(None)),
                    ScheduledJob.workspace_id.in_(self.workspace_ids(user)),
                ),
            )
        )
        if job is None:
            raise NotFound("Job not found")
        if job.workspace_id is not None:
            self.workspace(user, job.workspace_id, roles)
        return job

    def job_run(self, user: User, run_id: str) -> JobRun:
        self.require_active(user)
        accessible_jobs = select(ScheduledJob.id).where(
            or_(
                and_(ScheduledJob.owner_id == user.id, ScheduledJob.workspace_id.is_(None)),
                ScheduledJob.workspace_id.in_(self.workspace_ids(user)),
            )
        )
        run = self.session.scalar(
            select(JobRun).where(JobRun.id == run_id, JobRun.job_id.in_(accessible_jobs))
        )
        if run is None:
            raise NotFound("Job run not found")
        if run.result is not None:
            report = self.session.scalar(select(Report).where(Report.job_run_id == run.id))
            if report is None:
                raise NotFound("Job run not found")
            self.report(user, report.id)
        return run

    def incident(
        self, user: User, incident_id: str, roles: Collection[str] | None = None
    ) -> Incident:
        self.require_active(user)
        incident = self.session.scalar(
            select(Incident).where(
                Incident.id == incident_id, Incident.workspace_id.in_(self.workspace_ids(user))
            )
        )
        if incident is None:
            raise NotFound("Incident not found")
        self.workspace(user, incident.workspace_id, roles)
        return incident

    def notification(self, user: User, notification_id: str) -> Notification:
        """Find an owned notification; callers MUST re-authorize its target before rendering."""
        self.require_active(user)
        notification = self.session.scalar(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user.id
            )
        )
        if notification is None:
            raise NotFound("Notification not found")
        return notification

    def publication_statement(self, user: User) -> Select:
        """Return a projection only, never the ORM incident or its events.

        Grants cover the exact scope and its direct children. Explicitly escalated,
        published critical summaries may travel from any descendant to a grant.
        Recursive UNION (distinct) also terminates defensively for corrupt cycles.
        """
        self.require_active(user)
        grants = select(ScopeGrant.scope_id).where(
            ScopeGrant.user_id == user.id, ScopeGrant.can_review.is_(True)
        )
        child = aliased(Scope)
        direct = select(child.id).where(child.parent_id.in_(grants))
        descendants = (
            select(Scope.id).where(Scope.id.in_(grants)).cte("granted_descendants", recursive=True)
        )
        descendants = descendants.union(
            select(Scope.id).join(descendants, Scope.parent_id == descendants.c.id)
        )
        allowed = or_(
            Workspace.id.in_(self.workspace_ids(user)),
            Workspace.scope_id.in_(grants),
            Workspace.scope_id.in_(direct),
            and_(
                Incident.escalated.is_(True),
                Incident.severity == "critical",
                Workspace.scope_id.in_(select(descendants.c.id)),
            ),
        )
        return (
            select(
                Incident.id,
                Incident.workspace_id,
                Workspace.scope_id,
                Incident.title,
                Incident.summary,
                Incident.severity,
                Incident.status,
                Incident.assignee_id,
                Incident.decision,
                Incident.resolution,
                Incident.published,
                Incident.escalated,
                Incident.created_at,
                Incident.updated_at,
            )
            .join(Workspace, Workspace.id == Incident.workspace_id)
            .where(
                Incident.published.is_(True), Workspace.classification <= user.clearance, allowed
            )
        )

    def visible_publications(self, user: User, *, limit: int = 200) -> list[dict[str, Any]]:
        stmt = (
            self.publication_statement(user)
            .order_by(Incident.created_at.desc(), Incident.id)
            .limit(limit)
        )
        return [dict(row) for row in self.session.execute(stmt).mappings()]

    def publication(self, user: User, incident_id: str) -> dict[str, Any]:
        row = (
            self.session.execute(self.publication_statement(user).where(Incident.id == incident_id))
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Publication not found")
        return dict(row)
