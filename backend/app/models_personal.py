"""Personal assistant records. Original captures are separate from derived knowledge.

The existing Note, Memory, Reminder and Project remain the authoritative content
objects. These tables add capture provenance, filing, settings and reversible
actions instead of creating a second set of content repositories.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Identified, Mutable


class AssistantSettings(Mutable, Base):
    __tablename__ = "assistant_settings"
    __table_args__ = (
        CheckConstraint("reminder_mode IN ('off','suggest','automatic')", name="reminder_mode"),
        CheckConstraint("memory_mode IN ('off','suggest','automatic')", name="memory_mode"),
        CheckConstraint(
            "organization_mode IN ('off','suggest','automatic')", name="organization_mode"
        ),
        CheckConstraint("reminder_hour BETWEEN 0 AND 23", name="reminder_hour"),
        Index("ix_assistant_settings_daily_due", "next_checkin_at"),
        Index("ix_assistant_settings_organization_due", "next_organization_at"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    reminder_mode: Mapped[str] = mapped_column(
        String(20), default="suggest", server_default=sql_text("'suggest'")
    )
    memory_mode: Mapped[str] = mapped_column(
        String(20), default="suggest", server_default=sql_text("'suggest'")
    )
    organization_mode: Mapped[str] = mapped_column(
        String(20), default="suggest", server_default=sql_text("'suggest'")
    )
    daily_checkin_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    daily_checkin_time: Mapped[str] = mapped_column(
        String(5), default="17:00", server_default=sql_text("'17:00'")
    )
    reminder_hour: Mapped[int] = mapped_column(Integer, default=13, server_default=sql_text("13"))
    next_checkin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_organization_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalCapture(Identified, Mutable, Base):
    __tablename__ = "personal_captures"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_personal_captures_owner_request"),
        UniqueConstraint("id", "owner_id", name="uq_personal_captures_id_owner"),
        CheckConstraint(
            "status IN ('saved','classifying','classified','classification_failed','applied','undone')",
            name="status",
        ),
        Index("ix_personal_captures_owner_created", "owner_id", "created_at"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    data_date: Mapped[date] = mapped_column(Date, nullable=False)
    note_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("notes.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), default="saved", server_default=sql_text("'saved'")
    )
    classification: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    classification_error: Mapped[str | None] = mapped_column(String(500))
    classification_model: Mapped[str | None] = mapped_column(String(200))
    explicit_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification_token: Mapped[str | None] = mapped_column(String(36))
    classification_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalTask(Identified, Mutable, Base):
    __tablename__ = "personal_tasks"
    __table_args__ = (
        UniqueConstraint("capture_id", name="uq_personal_tasks_capture"),
        ForeignKeyConstraint(
            ["capture_id", "owner_id"],
            ["personal_captures.id", "personal_captures.owner_id"],
            name="fk_personal_tasks_capture_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('proposed','open','done','dismissed')", name="status"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    capture_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="proposed", server_default=sql_text("'proposed'")
    )
    suggested_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_basis: Mapped[str] = mapped_column(
        String(80), default="unscheduled", server_default=sql_text("'unscheduled'")
    )
    reminder_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("reminders.id", ondelete="SET NULL")
    )


class ProjectFolder(Identified, Mutable, Base):
    __tablename__ = "project_folders"
    __table_args__ = (
        UniqueConstraint(
            "id", "owner_id", "project_id", name="uq_project_folders_id_owner_project"
        ),
        ForeignKeyConstraint(
            ["project_id", "owner_id"],
            ["projects.id", "projects.owner_id"],
            name="fk_project_folders_project_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_id", "owner_id", "project_id"],
            ["project_folders.id", "project_folders.owner_id", "project_folders.project_id"],
            name="fk_project_folders_parent_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        Index(
            "uq_project_folders_sibling_name",
            "project_id",
            "parent_id",
            "name",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class NotePlacement(Mutable, Base):
    __tablename__ = "note_placements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "owner_id"],
            ["notes.id", "notes.owner_id"],
            name="fk_note_placements_note_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["folder_id", "owner_id", "project_id"],
            ["project_folders.id", "project_folders.owner_id", "project_folders.project_id"],
            name="fk_note_placements_folder_scope",
            ondelete="CASCADE",
        ),
    )
    note_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    folder_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)


class PersonalAction(Identified, Mutable, Base):
    __tablename__ = "personal_actions"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_personal_actions_owner_request"),
        CheckConstraint(
            "status IN ('pending','applied','undone','cancelled','failed')", name="status"
        ),
        Index("ix_personal_actions_owner_created", "owner_id", "created_at"),
        Index("ix_personal_actions_due", "status", "undo_until"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(180), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="applied", server_default=sql_text("'applied'")
    )
    resource_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    undo_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationChange(Identified, Mutable, Base):
    __tablename__ = "organization_changes"
    __table_args__ = (CheckConstraint("status IN ('preview','applied','undone')", name="status"),)
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="preview", server_default=sql_text("'preview'")
    )
    moves: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )
    action_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("personal_actions.id", ondelete="SET NULL")
    )


class DailyCheckin(Identified, Mutable, Base):
    __tablename__ = "daily_checkins"
    __table_args__ = (
        UniqueConstraint("owner_id", "local_date", name="uq_daily_checkins_owner_date"),
        CheckConstraint("status IN ('active','completed','skipped')", name="status"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=sql_text("'active'")
    )
    prompts: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capture_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("personal_captures.id", ondelete="SET NULL")
    )
