"""Scoped workspace and shared-resource queries; subscriptions never grant access."""

from collections.abc import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Conversation,
    Document,
    Incident,
    JobRun,
    Membership,
    Message,
    Report,
    ScheduledJob,
    Scope,
    User,
)
from .access import AccessRepository, NotFound


class WorkspaceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.access = AccessRepository(session)

    def reviewable_scopes(self, user: User) -> Sequence[Scope]:
        return self.access.reviewable_scopes(user)

    def members(self, user: User, workspace_id: str) -> Sequence[Membership]:
        self.access.workspace(user, workspace_id)
        return self.session.scalars(
            select(Membership)
            .where(Membership.workspace_id == workspace_id)
            .order_by(Membership.user_id)
        ).all()

    def documents(
        self, user: User, *, workspace_id: str | None = None, project_id: str | None = None
    ) -> Sequence[Document]:
        self.access.require_active(user)
        if workspace_id is not None and project_id is not None:
            raise ValueError("Choose a workspace or a private project")
        stmt = select(Document)
        if workspace_id is not None:
            self.access.workspace(user, workspace_id)
            stmt = stmt.where(Document.workspace_id == workspace_id)
        else:
            stmt = stmt.where(Document.owner_id == user.id)
            if project_id is not None:
                self.access.project(user, project_id)
                stmt = stmt.where(Document.project_id == project_id)
        return self.session.scalars(stmt.order_by(Document.created_at.desc(), Document.id)).all()

    def conversations(
        self, user: User, *, workspace_id: str | None = None, project_id: str | None = None
    ) -> Sequence[Conversation]:
        self.access.require_active(user)
        if workspace_id is not None and project_id is not None:
            raise ValueError("Choose a workspace or a private project")
        stmt = select(Conversation)
        if workspace_id is not None:
            self.access.workspace(user, workspace_id)
            stmt = stmt.where(Conversation.workspace_id == workspace_id)
        else:
            stmt = stmt.where(Conversation.owner_id == user.id)
            if project_id is not None:
                self.access.project(user, project_id)
                stmt = stmt.where(Conversation.project_id == project_id)
        return self.session.scalars(
            stmt.order_by(Conversation.updated_at.desc(), Conversation.id)
        ).all()

    def create_conversation(
        self,
        user: User,
        *,
        title: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> Conversation:
        self.access.require_active(user)
        if workspace_id is not None and project_id is not None:
            raise ValueError("Choose a workspace or a private project")
        if workspace_id is not None:
            self.access.workspace(user, workspace_id, roles=("member", "manager"))
        if project_id is not None:
            self.access.project(user, project_id)
        conversation = Conversation(
            owner_id=user.id if workspace_id is None else None,
            workspace_id=workspace_id,
            project_id=project_id,
            title=title,
        )
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def messages(self, user: User, conversation_id: str) -> Sequence[Message]:
        self.access.conversation(user, conversation_id)
        messages = self.session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        ).all()
        return [
            message
            for message in messages
            if message.role != "assistant"
            or self.access.source_refs_authorized(user, message.source_refs)
        ]

    def incidents(
        self, user: User, *, workspace_id: str | None = None, limit: int = 200
    ) -> Sequence[Incident]:
        if workspace_id is not None:
            self.access.workspace(user, workspace_id)
            stmt = select(Incident).where(Incident.workspace_id == workspace_id)
        else:
            stmt = select(Incident).where(
                Incident.workspace_id.in_(self.access.workspace_ids(user))
            )
        return self.session.scalars(
            stmt.order_by(Incident.created_at.desc(), Incident.id).limit(limit)
        ).all()

    def jobs(self, user: User) -> Sequence[ScheduledJob]:
        self.access.require_active(user)
        return self.session.scalars(
            select(ScheduledJob)
            .where(
                or_(
                    and_(ScheduledJob.owner_id == user.id, ScheduledJob.workspace_id.is_(None)),
                    ScheduledJob.workspace_id.in_(self.access.workspace_ids(user)),
                )
            )
            .order_by(ScheduledJob.created_at.desc(), ScheduledJob.id)
        ).all()

    def job_runs(self, user: User, job_id: str, *, limit: int = 100) -> Sequence[JobRun]:
        self.access.job(user, job_id)
        runs = self.session.scalars(
            select(JobRun)
            .where(JobRun.job_id == job_id)
            .order_by(JobRun.scheduled_for.desc(), JobRun.id)
            .limit(limit)
        ).all()
        visible = []
        for run in runs:
            try:
                visible.append(self.access.job_run(user, run.id))
            except NotFound:
                continue
        return visible

    def reports(self, user: User, *, workspace_id: str | None = None) -> Sequence[Report]:
        self.access.require_active(user)
        if workspace_id is not None:
            self.access.workspace(user, workspace_id)
            stmt = select(Report).where(Report.workspace_id == workspace_id)
        else:
            stmt = select(Report).where(Report.owner_id == user.id)
        reports = self.session.scalars(stmt.order_by(Report.created_at.desc(), Report.id)).all()
        visible = []
        for report in reports:
            try:
                visible.append(self.access.report(user, report.id))
            except NotFound:
                continue
        return visible
