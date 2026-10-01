"""Provider accounts, bounded calendar cache, reviewed meeting artifacts and delivery outbox."""

from datetime import datetime
from typing import Any
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Identified, Mutable, utcnow


class Connection(Identified, Mutable, Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('microsoft','zoom','influxdb','teams_workflow')", name="provider"
        ),
        CheckConstraint("status IN ('pending','connected','error','disconnected')", name="status"),
        Index("ix_connections_sync", "provider", "status", "next_sync_at"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    encrypted_credentials: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default=text("'pending'")
    )
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthAttempt(Identified, Base):
    __tablename__ = "oauth_attempts"
    __table_args__ = (
        UniqueConstraint("state_hash"),
        Index("ix_oauth_attempts_expiry", "expires_at"),
    )
    connection_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    state_hash: Mapped[str] = mapped_column(String(64))
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalendarEvent(Identified, Mutable, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("connection_id", "calendar_id", "provider_id"),
        Index("ix_calendar_events_window", "owner_id", "starts_at", "ends_at"),
    )
    connection_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("connections.id", ondelete="CASCADE")
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    calendar_id: Mapped[str] = mapped_column(String(1024))
    provider_id: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    busy: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class MeetingRoom(Identified, Mutable, Base):
    __tablename__ = "meeting_rooms"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','draft','reviewed','published','archived')", name="status"
        ),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="open")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    minutes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    workspace_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"
    room_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("meeting_rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingArtifact(Identified, Base):
    __tablename__ = "meeting_artifacts"
    __table_args__ = (
        UniqueConstraint("room_id", "content_hash"),
        CheckConstraint("kind IN ('transcript','minutes','notes')", name="kind"),
    )
    room_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("meeting_rooms.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_label: Mapped[str] = mapped_column(String(300))
    provider_reference: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    imported_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )


class MeetingDistribution(Identified, Base):
    __tablename__ = "meeting_distributions"
    __table_args__ = (UniqueConstraint("room_id", "revision", "audience_key"),)
    room_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("meeting_rooms.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    audience_key: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))


class Delivery(Identified, Mutable, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id"),
        CheckConstraint("channel IN ('email','teams')", name="channel"),
        CheckConstraint(
            "status IN ('pending','sending','sent','cancelled','failed','unknown')", name="status"
        ),
        Index("ix_deliveries_due", "status", "available_at"),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("connections.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    source_refs: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    request_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    undo_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingExtraction(Identified, Mutable, Base):
    __tablename__ = "meeting_extractions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
        ),
        CheckConstraint("phase IN ('map','reduce','finalize')", name="phase"),
        CheckConstraint("(lease_until IS NULL) = (lease_token IS NULL)", name="lease_pair"),
        Index("ix_meeting_extractions_claim", "status", "available_at", "lease_until"),
    )
    room_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("meeting_rooms.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    artifact_manifest: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    chunks: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    partials: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    summary_work: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    phase: Mapped[str] = mapped_column(String(20), default="map")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    error: Mapped[str | None] = mapped_column(String(500))
