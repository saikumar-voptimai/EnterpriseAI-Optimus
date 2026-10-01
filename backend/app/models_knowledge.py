"""Versioned assistant context and searchable knowledge; PostgreSQL is authoritative."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, Identified, Mutable


class WorkspaceBrief(Identified, Base):
    __tablename__ = "workspace_briefs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "revision", name="uq_workspace_briefs_revision"),
        CheckConstraint("status IN ('draft', 'published', 'superseded')", name="status"),
        Index(
            "uq_workspace_briefs_published",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserPreference(Identified, Mutable, Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_preferences_user"),)
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(80), default="English", nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Kolkata", nullable=False)
    units: Mapped[str] = mapped_column(String(40), default="SI", nullable=False)
    response_style: Mapped[str] = mapped_column(String(80), default="concise", nullable=False)
    personal_instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)


class DocumentRevision(Identified, Mutable, Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision", name="uq_document_revisions_revision"),
        CheckConstraint(
            "index_status IN ('lexical_ready', 'vector_pending', 'indexing', 'ready', 'failed', 'disabled')",
            name="index_status",
        ),
        Index(
            "uq_document_revisions_current",
            "document_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_document_revisions_index_queue", "index_status", "retry_at", "lease_until"),
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    original_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, deferred=True)
    index_status: Mapped[str] = mapped_column(String(24), default="lexical_ready", nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT")
    )


class KnowledgeChunk(Identified, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("revision_id", "ordinal", name="uq_knowledge_chunks_revision_ordinal"),
        CheckConstraint(
            "ordinal >= 0 AND start_offset >= 0 AND end_offset >= start_offset", name="offsets"
        ),
        Index("ix_knowledge_chunks_search", "search_vector", postgresql_using="gin"),
    )
    revision_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_revisions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, body)", persisted=True)
    )
    embedding: Mapped[Any | None] = mapped_column(Vector())


class ConversationSummary(Identified, Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "completed_turns", name="uq_conversation_summaries_turns"
        ),
    )
    conversation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    completed_turns: Mapped[int] = mapped_column(Integer, nullable=False)
    through_message_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("messages.id", ondelete="CASCADE")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)


# The default embedding profile has an ANN index. Other configured dimensions
# retain exact filtered search until their own measured index is provisioned.
from sqlalchemy import func, literal_column

Index(
    "ix_knowledge_chunks_embedding_1536",
    literal_column("(embedding::vector(1536))").label("embedding_1536"),
    _table=KnowledgeChunk.__table__,
    postgresql_using="hnsw",
    postgresql_ops={"embedding_1536": "vector_cosine_ops"},
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_where=func.vector_dims(KnowledgeChunk.embedding) == 1536,
)
