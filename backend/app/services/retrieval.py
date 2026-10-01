"""Hybrid PostgreSQL retrieval with permission filters before ranking and RRF fusion."""

import math
from sqlalchemy import cast, func, select, text, literal_column
from pgvector.sqlalchemy import Vector
from app.config import get_settings
from app.models import Document, DocumentChunk
from app.models_knowledge import DocumentRevision, KnowledgeChunk
from app.repositories.knowledge import KnowledgeRepository
from app.services.errors import ProviderError, ServiceError


class RetrievalService:
    def __init__(self, session, settings=None):
        self.session = session
        self.settings = settings or get_settings()
        self.repository = KnowledgeRepository(session)
        self.last_status = {"mode": "lexical", "reason": None}

    @staticmethod
    def record(chunk, revision, document, mode):
        return {
            "type": "document",
            "id": str(document.id),
            "title": document.title,
            "chunk": chunk.ordinal,
            "chunk_id": str(chunk.id),
            "revision_id": str(revision.id),
            "document_revision": revision.revision,
            "version": document.updated_at.isoformat(),
            "text": chunk.body,
            "location": chunk.location,
            "retrieval": mode,
        }

    def search(
        self,
        user,
        query,
        workspace_id=None,
        project_id=None,
        limit=12,
        query_embedding=None,
        vector_candidates=None,
    ):
        limit = max(1, min(int(limit), 40))
        statement = self.repository.chunks(user, workspace_id, project_id)
        terms = func.websearch_to_tsquery("simple", (query or "")[:8000])
        lexical = (
            statement.where(KnowledgeChunk.search_vector.op("@@")(terms))
            .order_by(
                func.ts_rank_cd(KnowledgeChunk.search_vector, terms).desc(),
                Document.updated_at.desc(),
                KnowledgeChunk.id,
            )
            .limit(limit * 3)
        )
        lexical_rows = list(self.session.execute(lexical)) if query.strip() else []
        vector_rows = []
        if vector_candidates:
            # Qdrant ranked these IDs; the audience/current-revision SQL decides
            # which of them this caller may actually receive.
            order = {str(identifier): rank for rank, identifier in enumerate(vector_candidates)}
            candidate_statement = statement.where(
                KnowledgeChunk.id.in_(list(order)),
                DocumentRevision.embedding_model == self.settings.embedding_model,
                DocumentRevision.embedding_dimensions == self.settings.embedding_dimensions,
            )
            vector_rows = sorted(
                self.session.execute(candidate_statement), key=lambda row: order[str(row[0].id)]
            )[: limit * 3]
        elif query_embedding is not None:
            if not query_embedding or not all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in query_embedding
            ):
                raise ServiceError("Invalid query embedding.", 502)
            distance_column = KnowledgeChunk.embedding
            vector_base = statement
            if len(query_embedding) == 1536:
                # Match the default-profile partial expression index exactly.
                self.session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
                vector_base = statement.where(
                    func.vector_dims(KnowledgeChunk.embedding) == literal_column("1536")
                )
                distance_column = cast(KnowledgeChunk.embedding, Vector(1536))
            vector_statement = (
                vector_base.where(
                    DocumentRevision.embedding_model == self.settings.embedding_model,
                    DocumentRevision.embedding_dimensions == len(query_embedding),
                    KnowledgeChunk.embedding.is_not(None),
                )
                .order_by(distance_column.cosine_distance(query_embedding))
                .limit(limit * 3)
            )
            vector_rows = list(self.session.execute(vector_statement))
        # Each branch has already constrained audience and current revision in SQL.
        ranked, scores = {}, {}
        for mode, rows in (("lexical", lexical_rows), ("semantic", vector_rows)):
            for rank, (chunk, revision, document) in enumerate(rows, 1):
                key = str(chunk.id)
                if key in ranked:
                    ranked[key]["retrieval"] = "hybrid"
                else:
                    ranked[key] = self.record(chunk, revision, document, mode)
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
        results = [
            ranked[key] for key in sorted(scores, key=lambda key: (-scores[key], key))[:limit]
        ]
        # Older installations may have unrecoverable originals but valid extracted
        # chunks. Include them until a revision is backfilled; never a recent-80 cap.
        predicate = self.repository.documents_predicate(user, workspace_id, project_id)
        legacy = (
            select(DocumentChunk, Document)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(
                predicate,
                ~select(DocumentRevision.id)
                .where(DocumentRevision.document_id == Document.id)
                .exists(),
            )
        )
        legacy_base = legacy
        if query.strip():
            legacy_terms = func.to_tsvector("simple", DocumentChunk.body)
            legacy = legacy.where(legacy_terms.op("@@")(terms)).order_by(
                func.ts_rank_cd(legacy_terms, terms).desc(), Document.id, DocumentChunk.ordinal
            )
        else:
            legacy = legacy.order_by(Document.updated_at.desc(), Document.id, DocumentChunk.ordinal)
        for chunk, document in self.session.execute(legacy.limit(limit)):
            if len(results) >= limit:
                break
            results.append(
                {
                    "type": "document",
                    "id": document.id,
                    "title": document.title,
                    "chunk": chunk.ordinal,
                    "text": chunk.body,
                    "version": document.updated_at.isoformat(),
                    "retrieval": "legacy_lexical",
                }
            )
        if not results:
            # Broad requests such as "summarize" still receive a small deterministic
            # sample; full-corpus relevance was attempted before recency selection.
            for chunk, revision, document in self.session.execute(
                statement.order_by(
                    Document.updated_at.desc(), Document.id, KnowledgeChunk.ordinal
                ).limit(limit)
            ):
                results.append(self.record(chunk, revision, document, "recent_sample"))
            if not results:
                for chunk, document in self.session.execute(
                    legacy_base.order_by(
                        Document.updated_at.desc(), Document.id, DocumentChunk.ordinal
                    ).limit(limit)
                ):
                    results.append(
                        {
                            "type": "document",
                            "id": document.id,
                            "title": document.title,
                            "chunk": chunk.ordinal,
                            "text": chunk.body,
                            "version": document.updated_at.isoformat(),
                            "retrieval": "legacy_lexical",
                        }
                    )
        self.last_status = {
            "mode": "hybrid" if vector_rows else "lexical",
            "reason": (
                None if vector_rows else "Semantic retrieval unavailable or awaiting indexing."
            ),
        }
        return results

    async def search_hybrid(
        self, user, query, workspace_id=None, project_id=None, limit=12, gateway=None
    ):
        # Authorize before sending even the query to an external embedding provider.
        self.repository.documents_predicate(user, workspace_id, project_id)
        if (
            workspace_id
            and not self.repository.access.workspace(user, workspace_id).external_ai_enabled
        ):
            return []
        user_id = user.id
        self.session.rollback()
        embedding = None
        if (
            gateway
            and getattr(self.settings, "embeddings_enabled", False)
            and self.settings.allow_external_ai
            and self.settings.openrouter_api_key
        ):
            try:
                vectors = await gateway.embed(
                    [query[:8000]],
                    model=self.settings.embedding_model,
                    dimensions=self.settings.embedding_dimensions,
                )
                embedding = vectors[0]
            except (ProviderError, ServiceError):
                self.last_status = {
                    "mode": "lexical",
                    "reason": "Embedding provider unavailable; lexical retrieval used.",
                }
        candidates = None
        if embedding is not None and self.settings.vector_backend == "qdrant":
            from app.services.vector_index import QdrantIndex, audience_key

            try:
                candidates = await QdrantIndex(self.settings).search(
                    embedding,
                    audience=audience_key(workspace_id, user_id),
                    project_id=None if workspace_id else project_id,
                    limit=max(1, min(int(limit), 40)) * 3,
                )
            except ProviderError:
                self.last_status = {
                    "mode": "lexical",
                    "reason": "Vector index unavailable; lexical retrieval used.",
                }
            embedding = None
        from app.models import User

        self.session.expire_all()
        user = self.session.get(User, user_id)
        if user is None:
            raise ServiceError("The user is no longer available.", 403)
        return self.search(
            user, query, workspace_id, project_id, limit, embedding, vector_candidates=candidates
        )
