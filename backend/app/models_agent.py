"""Durable agent execution records and fenced LangGraph checkpoints."""

from datetime import datetime
from typing import Any
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base, Identified, Mutable, utcnow


class AgentRun(Identified, Mutable, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
        ),
        CheckConstraint("(lease_token IS NULL) = (lease_until IS NULL)", name="lease_pair"),
        UniqueConstraint("owner_id", "request_key", name="uq_agent_runs_owner_request"),
        Index("ix_agent_runs_claim", "status", "available_at", "lease_until"),
        Index(
            "uq_agent_runs_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("conversation_id IS NOT NULL AND status IN ('queued','running')"),
        ),
    )
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("projects.id", ondelete="SET NULL")
    )
    user_message_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("messages.id", ondelete="SET NULL")
    )
    assistant_message_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("messages.id", ondelete="SET NULL")
    )
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="chat", server_default=text("'chat'"))
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default=text("'queued'")
    )
    stage: Mapped[str] = mapped_column(
        String(60), default="queued", server_default=text("'queued'")
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    context_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class AgentRunEvent(Identified, Base):
    __tablename__ = "agent_run_events"
    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(100))
    payload_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class AgentCheckpointWrite(Base):
    __tablename__ = "agent_checkpoint_writes"
    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    index: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
