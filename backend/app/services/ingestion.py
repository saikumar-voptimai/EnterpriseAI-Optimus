"""Transactional originals/revisions and a leased, retryable embedding queue."""

import math
from datetime import timedelta
from pathlib import PurePath
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select
from app.config import get_settings
from app.models import AuditEvent, Document, DocumentChunk, User, Workspace, utcnow
from app.models_knowledge import DocumentRevision, KnowledgeChunk
from app.repositories import AccessRepository, AuthorizationError, NotFound
from app.repositories.knowledge import KnowledgeRepository
from app.services.documents import ExtractedDocument, extract_document, chunk_text
from app.services.errors import ProviderError, ServiceError


class IngestionService:
    def __init__(self, session, settings=None):
        self.session = session
        self.settings = settings or get_settings()
        self.access = AccessRepository(session)

    def ingest(
        self, user, data, filename, title=None, workspace_id=None, project_id=None, document_id=None
    ):
        self.access.require_active(user)
        if workspace_id and project_id:
            raise ServiceError("Choose a workspace or personal project.", 422)
        filename = PurePath(filename.replace("\\", "/")).name[:240] or "document.txt"
        extracted = extract_document(data, filename, max_bytes=self.settings.max_upload_bytes)
        if document_id:
            document = self.access.document(user, document_id, roles={"member", "manager"})
            document = self.session.scalar(
                select(Document).where(Document.id == document.id).with_for_update()
            )
            workspace_id, project_id = document.workspace_id, document.project_id
            if document.content_hash == extracted.content_hash:
                current = KnowledgeRepository(self.session).current_revision(user, document.id)
                if current:
                    return document
        else:
            if workspace_id:
                self.access.workspace(user, workspace_id, roles={"member", "manager"})
            if project_id:
                self.access.project(user, project_id)
            document = Document(
                owner_id=None if workspace_id else user.id,
                workspace_id=workspace_id,
                project_id=project_id,
                created_by=user.id,
                title=(title or filename)[:300],
                filename=filename,
                media_type=extracted.media_type,
                content_hash=extracted.content_hash,
                body=extracted.body,
            )
            self.session.add(document)
            self.session.flush()
        document.filename, document.media_type = filename, extracted.media_type
        document.content_hash, document.body = extracted.content_hash, extracted.body
        if title:
            document.title = title[:300]
        document.updated_at = utcnow()
        return self._write_revision(user, document, extracted, data)

    def _write_revision(self, user, document, extracted, original_bytes):
        previous = self.session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.document_id == document.id, DocumentRevision.is_current.is_(True)
            )
        )
        if previous:
            previous.is_current = False
            self.session.flush()
        revision_number = (
            self.session.scalar(
                select(func.max(DocumentRevision.revision)).where(
                    DocumentRevision.document_id == document.id
                )
            )
            or 0
        ) + 1
        fragments = chunk_text(extracted.body)
        revision = DocumentRevision(
            document_id=document.id,
            revision=revision_number,
            is_current=True,
            content_hash=extracted.content_hash,
            original_bytes=original_bytes,
            original_size=len(original_bytes) if original_bytes else 0,
            filename=document.filename,
            media_type=extracted.media_type,
            extracted_text=extracted.body,
            index_status="vector_pending" if self._can_embed(document) else "lexical_ready",
            chunk_count=len(fragments),
            indexed_count=0,
            attempts=0,
            created_by=user.id,
        )
        self.session.add(revision)
        self.session.flush()
        self.session.add_all(
            KnowledgeChunk(
                revision_id=revision.id,
                ordinal=i,
                body=part.body,
                start_offset=part.start_offset,
                end_offset=part.end_offset,
                location=part.location,
            )
            for i, part in enumerate(fragments)
        )
        self.session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        self.session.add(
            AuditEvent(
                actor_id=user.id,
                action="document_revision_created",
                resource_type="document",
                resource_id=document.id,
                details={
                    "revision": revision_number,
                    "bytes": revision.original_size,
                    "chunks": len(fragments),
                    "original_available": original_bytes is not None,
                },
            )
        )
        self.session.flush()
        return document

    def backfill(self, user, document_id):
        """Upgrade retained v1 text without claiming that discarded originals exist."""
        document = self.access.document(user, document_id, roles={"member", "manager"})
        document = self.session.scalar(
            select(Document).where(Document.id == document.id).with_for_update()
        )
        if KnowledgeRepository(self.session).current_revision(user, document_id) is not None:
            return document
        extracted = ExtractedDocument(document.body, document.content_hash, document.media_type, [])
        return self._write_revision(user, document, extracted, None)

    def _can_embed(self, document):
        settings = self.settings
        if not (
            getattr(settings, "embeddings_enabled", False)
            and settings.allow_external_ai
            and settings.openrouter_api_key
        ):
            return False
        return not document.workspace_id or bool(
            self.session.get(Workspace, document.workspace_id).external_ai_enabled
        )

    def retry(self, user, document_id):
        revision = KnowledgeRepository(self.session).current_revision(user, document_id, write=True)
        if revision is None:
            self.backfill(user, document_id)
            revision = KnowledgeRepository(self.session).current_revision(
                user, document_id, write=True
            )
        document = self.access.document(user, document_id, roles={"member", "manager"})
        if not self._can_embed(document):
            raise ServiceError("Enable embeddings and external AI for this audience first.", 409)
        if revision.lease_until and revision.lease_until > utcnow():
            raise ServiceError("This document is currently being indexed.", 409)
        revision.index_status, revision.attempts, revision.error = "vector_pending", 0, None
        revision.retry_at, revision.lease_until, revision.lease_token = None, None, None
        if (
            revision.embedding_model != self.settings.embedding_model
            or revision.embedding_dimensions != self.settings.embedding_dimensions
        ):
            for chunk in self.session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.revision_id == revision.id)
            ):
                chunk.embedding = None
            revision.indexed_count = 0
        self.session.flush()
        return revision


class KnowledgeIndexer:
    """One bounded batch per tick; expired claims can be recovered by another worker."""

    def __init__(self, session_factory, settings=None, gateway=None):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        if gateway is None:
            from app.services.gateway import OpenRouterGateway

            gateway = OpenRouterGateway(self.settings)
        self.gateway = gateway

    async def run_once(self):
        if not (
            getattr(self.settings, "embeddings_enabled", False)
            and self.settings.allow_external_ai
            and self.settings.openrouter_api_key
        ):
            return False
        now, token = utcnow(), str(uuid4())
        with self.session_factory() as session:
            revision = session.scalar(
                select(DocumentRevision)
                .where(
                    DocumentRevision.is_current.is_(True),
                    or_(
                        DocumentRevision.index_status == "vector_pending",
                        and_(
                            DocumentRevision.index_status == "indexing",
                            DocumentRevision.lease_until < now,
                        ),
                    ),
                    or_(DocumentRevision.retry_at.is_(None), DocumentRevision.retry_at <= now),
                )
                .order_by(DocumentRevision.created_at, DocumentRevision.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if revision is None:
                return False
            document = session.get(Document, revision.document_id)
            user = session.get(User, revision.created_by)
            try:
                AccessRepository(session).document(user, document.id)
                if not IngestionService(session, self.settings)._can_embed(document):
                    raise AuthorizationError("External AI is disabled")
            except (NotFound, AuthorizationError, AttributeError):
                revision.index_status, revision.error = (
                    "disabled",
                    "Document author or external AI authorization is no longer available.",
                )
                session.commit()
                return True
            batch = list(
                session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.revision_id == revision.id,
                        KnowledgeChunk.embedding.is_(None),
                    )
                    .order_by(KnowledgeChunk.ordinal)
                    .limit(getattr(self.settings, "embedding_batch_size", 32))
                )
            )
            if not batch:
                revision.index_status = "ready"
                session.commit()
                return True
            revision.index_status, revision.lease_token = "indexing", token
            revision.lease_until = now + timedelta(
                seconds=max(150, self.settings.openrouter_timeout_seconds + 30)
            )
            revision.attempts += 1
            revision_id, document_id, user_id = revision.id, document.id, user.id
            audience = (document.workspace_id, document.owner_id, document.project_id)
            inputs = [(chunk.id, chunk.body) for chunk in batch]
            session.commit()
        try:
            vectors = await self.gateway.embed(
                [body for _, body in inputs],
                model=self.settings.embedding_model,
                dimensions=self.settings.embedding_dimensions,
            )
            if len(vectors) != len(inputs):
                raise ProviderError("Embedding batch count did not match.")
            if any(
                len(vector) != self.settings.embedding_dimensions
                or not all(
                    isinstance(value, (int, float)) and math.isfinite(value) for value in vector
                )
                for vector in vectors
            ):
                raise ProviderError("Embedding values do not match the configured profile.")
            if self.settings.vector_backend == "qdrant":
                from app.services.vector_index import QdrantIndex, audience_key

                workspace_id, owner_id, project_id = audience
                await QdrantIndex(self.settings).upsert_revision(
                    document_id=document_id,
                    revision_id=revision_id,
                    audience=audience_key(workspace_id, owner_id),
                    project_id=None if workspace_id else project_id,
                    points=[
                        (identifier, vector) for (identifier, _), vector in zip(inputs, vectors)
                    ],
                )
            error = None
        except (ProviderError, ServiceError) as exc:
            vectors, error = None, str(exc)[:400]
        with self.session_factory() as session:
            revision = session.scalar(
                select(DocumentRevision).where(DocumentRevision.id == revision_id).with_for_update()
            )
            if (
                revision is None
                or revision.lease_token != token
                or not revision.is_current
                or revision.lease_until <= utcnow()
            ):
                return True
            user, document = session.get(User, user_id), session.get(Document, document_id)
            try:
                AccessRepository(session).document(user, document_id)
                if not IngestionService(session, self.settings)._can_embed(document):
                    raise AuthorizationError("External AI disabled")
            except (NotFound, AuthorizationError, AttributeError):
                revision.index_status, revision.error = (
                    "disabled",
                    "Authorization changed during indexing.",
                )
                revision.lease_token = revision.lease_until = None
                session.commit()
                return True
            revision.lease_token = revision.lease_until = None
            if error:
                revision.error = error
                revision.index_status = "failed" if revision.attempts >= 3 else "vector_pending"
                revision.retry_at = utcnow() + timedelta(
                    seconds=30 * 2 ** min(revision.attempts, 6)
                )
            else:
                for (identifier, _), embedding in zip(inputs, vectors):
                    session.get(KnowledgeChunk, identifier).embedding = embedding
                session.flush()
                revision.embedding_model, revision.embedding_dimensions = (
                    self.settings.embedding_model,
                    self.settings.embedding_dimensions,
                )
                revision.indexed_count = session.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.revision_id == revision.id,
                        KnowledgeChunk.embedding.is_not(None),
                    )
                )
                revision.index_status = (
                    "ready" if revision.indexed_count == revision.chunk_count else "vector_pending"
                )
                revision.attempts, revision.error, revision.retry_at = 0, None, None
            session.commit()
        return True
