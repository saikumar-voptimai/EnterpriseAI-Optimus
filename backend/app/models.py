"""Normalized PostgreSQL models; all application identifiers are UUID strings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_string() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Created:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()"), nullable=False
    )


class Identified(Created):
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        primary_key=True,
        default=uuid_string,
        server_default=text("gen_random_uuid()"),
    )


class Mutable:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=text("now()"),
        nullable=False,
    )


class User(Identified, Mutable, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("clearance BETWEEN 1 AND 3", name="clearance"),
        CheckConstraint("email = lower(email) AND length(email) > 3", name="email_lowercase"),
        UniqueConstraint("email", name="uq_users_email"),
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_service: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    clearance: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


class AuthSession(Identified, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LoginAttempt(Identified, Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        UniqueConstraint("key", name="uq_login_attempts_key"),
        CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
        Index("ix_login_attempts_window_start", "window_start"),
    )
    key: Mapped[str] = mapped_column(String(320), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Scope(Identified, Mutable, Base):
    __tablename__ = "scopes"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        Index(
            "uq_scopes_name_parent",
            "parent_id",
            "name",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scopes.id", ondelete="RESTRICT"), index=True
    )


class ScopeGrant(Created, Base):
    __tablename__ = "scope_grants"
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("scopes.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    can_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Workspace(Identified, Mutable, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("classification BETWEEN 1 AND 3", name="classification"),
        UniqueConstraint("scope_id", "name", name="uq_workspaces_scope_name"),
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    scope_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scopes.id", ondelete="RESTRICT"), index=True
    )
    classification: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    external_ai_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class Membership(Created, Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint("role IN ('viewer', 'member', 'manager')", name="role"),)
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), default="member", server_default=text("'member'"))


class Project(Identified, Mutable, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("id", "owner_id", name="uq_projects_id_owner"),)
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Note(Identified, Mutable, Base):
    __tablename__ = "notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_id"],
            ["projects.id", "projects.owner_id"],
            name="fk_notes_project_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_id", name="uq_notes_id_owner"),
        CheckConstraint(
            "kind IN ('observation', 'thought', 'hypothesis', 'decision', 'commitment', 'minutes')",
            name="kind",
        ),
        Index("ix_notes_owner_project", "owner_id", "project_id"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), default="thought", server_default=text("'thought'")
    )


class Memory(Identified, Mutable, Base):
    __tablename__ = "memories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "owner_id"],
            ["notes.id", "notes.owner_id"],
            name="fk_memories_note_owner",
            ondelete="CASCADE",
        ),
        Index("ix_memories_owner_forgotten", "owner_id", "forgotten_at"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    note_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    forgotten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reminder(Identified, Mutable, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "owner_id"],
            ["notes.id", "notes.owner_id"],
            name="fk_reminders_note_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('open', 'waiting', 'snoozed', 'done', 'cancelled')", name="status"
        ),
        Index("ix_reminders_due_status", "due_at", "status"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    note_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", server_default=text("'open'"))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Identified, Mutable, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "(owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)", name="audience_xor"
        ),
        CheckConstraint("project_id IS NULL OR owner_id IS NOT NULL", name="project_private"),
        ForeignKeyConstraint(
            ["project_id", "owner_id"],
            ["projects.id", "projects.owner_id"],
            name="fk_documents_project_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(content_hash) = 64", name="sha256_length"),
        Index("ix_documents_workspace_created", "workspace_id", "created_at"),
    )
    owner_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class DocumentChunk(Identified, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_document_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Conversation(Identified, Mutable, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "(owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)", name="audience_xor"
        ),
        CheckConstraint("project_id IS NULL OR owner_id IS NOT NULL", name="project_private"),
        ForeignKeyConstraint(
            ["project_id", "owner_id"],
            ["projects.id", "projects.owner_id"],
            name="fk_conversations_project_owner",
            ondelete="RESTRICT",
        ),
    )
    owner_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)


class Message(Identified, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )
    conversation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(200))
    usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )


class Incident(Identified, Mutable, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name="severity"),
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="status"),
        CheckConstraint(
            "NOT escalated OR (published AND severity = 'critical')",
            name="escalation_published_critical",
        ),
        UniqueConstraint("workspace_id", "request_id", name="uq_incidents_workspace_request"),
        Index("ix_incidents_workspace_status_created", "workspace_id", "status", "created_at"),
        Index("ix_incidents_published", "published", "escalated"),
    )
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), default="medium", server_default=text("'medium'")
    )
    status: Mapped[str] = mapped_column(String(20), default="open", server_default=text("'open'"))
    assignee_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reported_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    resolution: Mapped[str | None] = mapped_column(Text)
    published: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)


class IncidentEvent(Identified, Base):
    __tablename__ = "incident_events"
    incident_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class Subscription(Created, Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("scopes.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class Notification(Identified, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
        Index("ix_notifications_user_read_created", "user_id", "read_at", "created_at"),
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(500))


class ScheduledJob(Identified, Mutable, Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        CheckConstraint(
            "interval_minutes IS NULL OR interval_minutes BETWEEN 15 AND 20160",
            name="interval_range",
        ),
        CheckConstraint("status IN ('active', 'paused', 'completed')", name="status"),
        CheckConstraint(
            "approval_status IN ('legacy','pending','approved','rejected')", name="approval_status"
        ),
        CheckConstraint(
            "task_type IN ('analysis','validation','handover','production_comparison','morning_brief')",
            name="task_type",
        ),
        CheckConstraint(
            "dependency_policy IN ('pass','pass_warn','any')", name="dependency_policy"
        ),
        Index("ix_scheduled_jobs_status_next_run", "status", "next_run_at"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'")
    )
    external_ai_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    execution_user_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )
    task_type: Mapped[str] = mapped_column(
        String(40), default="analysis", server_default=text("'analysis'")
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    timezone: Mapped[str] = mapped_column(String(80), default="UTC", server_default=text("'UTC'"))
    approval_status: Mapped[str] = mapped_column(
        String(20), default="legacy", server_default=text("'legacy'")
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    approved_revision: Mapped[int | None] = mapped_column(Integer)
    activation_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dependency_policy: Mapped[str] = mapped_column(
        String(20), default="pass_warn", server_default=text("'pass_warn'")
    )


class JobDependency(Created, Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (CheckConstraint("job_id <> depends_on_id", name="not_self"),)
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("scheduled_jobs.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )


class JobRun(Identified, Mutable, Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "scheduled_for", name="uq_job_runs_job_scheduled"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'blocked')", name="status"
        ),
        CheckConstraint("trigger_kind IN ('scheduled','manual')", name="trigger_kind"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("(lease_until IS NULL) = (lease_token IS NULL)", name="lease_pair"),
        Index("ix_job_runs_claim", "status", "available_at", "lease_until"),
    )
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), index=True
    )
    trigger_kind: Mapped[str] = mapped_column(
        String(20), default="scheduled", server_default=text("'scheduled'")
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default=text("'queued'")
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Report(Identified, Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "(owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)", name="audience_xor"
        ),
        UniqueConstraint("job_run_id", name="uq_reports_job_run"),
    )
    owner_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    job_run_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("job_runs.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    result_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class AuditEvent(Identified, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_resource", "resource_type", "resource_id"),)
    actor_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class AppSetting(Identified, Mutable, Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_app_settings_key"),)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)


# Register each domain once so Alembic and tests see the complete schema.
from . import (
    models_agent,
    models_knowledge,
    models_personal,
    models_connections,
    models_governance,
)  # noqa: E402,F401
