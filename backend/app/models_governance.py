"""Versioned job approvals and narrowly scoped organizational administration."""

from datetime import datetime
from typing import Any
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base, Identified, Mutable


class ScopeRoleAssignment(Identified, Mutable, Base):
    __tablename__ = "scope_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "scope_id", name="uq_scope_role_assignments_user_scope"),
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scopes.id", ondelete="CASCADE"), index=True
    )
    manage_workspaces: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    manage_people: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )


class WorkspaceAutomation(Identified, Base):
    __tablename__ = "workspace_automations"
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True
    )
    service_user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )


class JobRevision(Identified, Base):
    __tablename__ = "job_revisions"
    __table_args__ = (UniqueConstraint("job_id", "revision", name="uq_job_revisions_job_revision"),)
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    submitted_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
