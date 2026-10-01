"""Audience predicates are applied in SQL before any lexical or vector ranking."""

from sqlalchemy import and_, select
from sqlalchemy.orm import load_only
from app.models import Document
from app.models_knowledge import DocumentRevision, KnowledgeChunk
from app.repositories import AccessRepository


class KnowledgeRepository:
    def __init__(self, session):
        self.session = session
        self.access = AccessRepository(session)

    def documents_predicate(self, user, workspace_id=None, project_id=None, external=True):
        self.access.require_active(user)
        if workspace_id:
            workspace = self.access.workspace(user, workspace_id)
            if external and not workspace.external_ai_enabled:
                # An empty SQL predicate, not filtering secret results after ranking.
                return Document.id.is_(None)
            return Document.workspace_id == workspace_id
        if project_id:
            self.access.project(user, project_id)
        predicate = and_(Document.owner_id == user.id, Document.workspace_id.is_(None))
        return and_(predicate, Document.project_id == project_id) if project_id else predicate

    def chunks(self, user, workspace_id=None, project_id=None, external=True):
        return (
            select(KnowledgeChunk, DocumentRevision, Document)
            .options(
                load_only(
                    KnowledgeChunk.id,
                    KnowledgeChunk.ordinal,
                    KnowledgeChunk.body,
                    KnowledgeChunk.location,
                ),
                load_only(DocumentRevision.id, DocumentRevision.revision),
                load_only(Document.id, Document.title, Document.updated_at),
            )
            .join(DocumentRevision, KnowledgeChunk.revision_id == DocumentRevision.id)
            .join(Document, DocumentRevision.document_id == Document.id)
            .where(
                DocumentRevision.is_current.is_(True),
                self.documents_predicate(user, workspace_id, project_id, external),
            )
        )

    def current_revision(self, user, document_id, *, write=False):
        self.access.document(user, document_id, roles={"member", "manager"} if write else None)
        return self.session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.document_id == document_id, DocumentRevision.is_current.is_(True)
            )
        )
