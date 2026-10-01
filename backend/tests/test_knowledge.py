"""Knowledge regressions: audience, revisions, older recall, real pgvector and retry fencing."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from app.config import Settings
from app.models import (
    Conversation,
    Document,
    DocumentChunk,
    Membership,
    Message,
    Note,
    Scope,
    User,
    Workspace,
    utcnow,
)
from app.models_knowledge import ConversationSummary, DocumentRevision, KnowledgeChunk
from app.repositories import AuthorizationError, NotFound
from app.services.context import ContextService
from app.services.documents import chunk_text
from app.services.errors import ProviderError, ServiceError
from app.services.gateway import Completion
from app.services.ingestion import IngestionService, KnowledgeIndexer
from app.services.preferences import PreferenceService
from app.services.retrieval import RetrievalService
from app.services.workspace_briefs import WorkspaceBriefService


@pytest.fixture
def settings():
    return Settings(
        allow_external_ai=True,
        openrouter_api_key="test",
        openrouter_models="test/model",
        openrouter_default_model="test/model",
        embeddings_enabled=True,
        embedding_dimensions=64,
        embedding_batch_size=2,
    )


def seed(db):
    user = User(
        email=f"knowledge-{uuid4()}@test.example",
        name="Engineer",
        password_hash="x",
        active=True,
        clearance=3,
    )
    other = User(
        email=f"other-{uuid4()}@test.example",
        name="Other",
        password_hash="x",
        active=True,
        clearance=3,
    )
    db.add_all([user, other])
    db.flush()
    scope = Scope(name="Plant", kind="plant")
    db.add(scope)
    db.flush()
    workspace = Workspace(
        name="Operations",
        description="Purpose: water treatment",
        scope_id=scope.id,
        created_by=user.id,
        classification=1,
        external_ai_enabled=True,
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="manager"))
    db.flush()
    return user, other, workspace


def test_token_chunks_preserve_unicode_offsets_and_overlap():
    text = "# Operations\n\n" + ("తాపన furnace 温度 " * 800)
    parts = chunk_text(text)
    assert len(parts) > 2
    assert parts[0].start_offset == 0 and parts[-1].end_offset == len(text)
    assert all(part.body == text[part.start_offset : part.end_offset] for part in parts)
    assert all(left.end_offset > right.start_offset for left, right in zip(parts, parts[1:]))
    assert parts[1].location["heading"] == "Operations"


def test_published_brief_preferences_and_midrun_change_validation(db, settings):
    user, other, workspace = seed(db)
    conversation = Conversation(workspace_id=workspace.id, title="Shared")
    db.add(conversation)
    db.flush()
    PreferenceService(db).update(
        user, {"language": "Telugu", "personal_instructions": "PRIVATE_PREFERENCE_MARKER"}
    )
    service = WorkspaceBriefService(db, settings)
    draft = service.create_draft(user, workspace.id, "DRAFT_MARKER")
    context = ContextService(db, settings)
    before = context.for_conversation(user, conversation, "Help")
    assert "water treatment" in before.messages[0]["content"]
    assert "DRAFT_MARKER" not in before.messages[0]["content"]
    assert "Telugu" in before.messages[0]["content"]
    assert "PRIVATE_PREFERENCE_MARKER" not in before.messages[0]["content"]
    service.publish(user, workspace.id, draft.id)
    with pytest.raises(ServiceError, match="settings changed"):
        context.validate_metadata(user, before.metadata, workspace.id)
    after = context.for_conversation(user, conversation, "Help")
    assert "DRAFT_MARKER" in after.messages[0]["content"]
    assert after.metadata["workspace_brief"]["revision"] == 1
    with pytest.raises(NotFound):
        service.create_draft(other, workspace.id, "No access")
    second = service.create_draft(user, workspace.id, "Second published")
    service.publish(user, workspace.id, second.id)
    assert draft.status == "superseded"


def test_ingestion_retains_original_and_revision_and_searches_current_only(db, settings):
    user, _, workspace = seed(db)
    ingestion = IngestionService(db, settings)
    document = ingestion.ingest(
        user, b"old cobalt instruction", "sop.txt", workspace_id=workspace.id
    )
    first = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == document.id))
    assert first.original_bytes == b"old cobalt instruction"
    assert first.index_status == "vector_pending"
    ingestion.ingest(user, b"new manganese instruction", "sop.txt", document_id=document.id)
    revisions = db.scalars(
        select(DocumentRevision)
        .where(DocumentRevision.document_id == document.id)
        .order_by(DocumentRevision.revision)
    ).all()
    assert [revision.is_current for revision in revisions] == [False, True]
    results = RetrievalService(db, settings).search(user, "manganese", workspace.id)
    assert results and all("cobalt" not in row["text"] for row in results)
    assert results[0]["document_revision"] == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
        )
        == 0
    )


def test_document_retrieval_has_no_recent_80_cutoff_and_never_crosses_audience(db, settings):
    user, other, workspace = seed(db)
    old = Document(
        workspace_id=workspace.id,
        title="Historic SOP",
        filename="old.txt",
        media_type="text/plain",
        content_hash="a" * 64,
        body="legacyquartz thermal instruction",
        created_by=user.id,
        created_at=utcnow() - timedelta(days=400),
    )
    db.add(old)
    db.flush()
    db.add(DocumentChunk(document_id=old.id, ordinal=0, body=old.body))
    for index in range(100):
        document = Document(
            workspace_id=workspace.id,
            title=f"Recent {index}",
            filename="new.txt",
            media_type="text/plain",
            content_hash="b" * 64,
            body="ordinary unrelated details",
            created_by=user.id,
        )
        db.add(document)
        db.flush()
        db.add(DocumentChunk(document_id=document.id, ordinal=0, body=document.body))
    secret = IngestionService(db, settings).ingest(
        other, b"legacyquartz PRIVATE_MARKER", "secret.txt"
    )
    db.flush()
    results = RetrievalService(db, settings).search(user, "legacyquartz", workspace.id)
    assert results[0]["id"] == old.id
    assert secret.id not in {row["id"] for row in results}
    with pytest.raises(NotFound):
        RetrievalService(db, settings).search(other, "legacyquartz", workspace.id)


@pytest.mark.parametrize("dimensions", [64, 1536])
def test_real_pgvector_semantic_search_and_model_profile_filter(db, settings, dimensions):
    settings.embedding_dimensions = dimensions
    user, _, workspace = seed(db)
    document = IngestionService(db, settings).ingest(
        user, b"blast furnace cooling procedure", "cooling.txt", workspace_id=workspace.id
    )
    revision = db.scalar(
        select(DocumentRevision).where(DocumentRevision.document_id == document.id)
    )
    chunk = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.revision_id == revision.id))
    revision.embedding_model, revision.embedding_dimensions, revision.index_status = (
        settings.embedding_model,
        dimensions,
        "ready",
    )
    vector = [1.0] + [0.0] * (dimensions - 1)
    chunk.embedding = vector
    db.flush()
    results = RetrievalService(db, settings).search(
        user, "an unrelated paraphrase", workspace.id, query_embedding=vector
    )
    assert results[0]["id"] == document.id and results[0]["retrieval"] == "semantic"
    workspace.external_ai_enabled = False
    db.flush()
    assert (
        RetrievalService(db, settings).search(user, "cooling", workspace.id, query_embedding=vector)
        == []
    )


class EmbeddingGateway:
    def __init__(self, fail=False):
        self.fail = fail

    async def embed(self, texts, model=None, dimensions=None):
        if self.fail:
            raise ProviderError("Embedding service unavailable", retryable=True)
        return [[1.0] + [0.0] * (dimensions - 1) for _ in texts]


def test_index_worker_retries_then_persists_real_embeddings(db, db_factory, settings):
    user, _, workspace = seed(db)
    document = IngestionService(db, settings).ingest(
        user, b"A usable SOP", "sop.txt", workspace_id=workspace.id
    )
    document_id = document.id
    db.commit()
    worker = KnowledgeIndexer(db_factory, settings, EmbeddingGateway(fail=True))
    assert asyncio.run(worker.run_once())
    db.expire_all()
    revision = db.scalar(
        select(DocumentRevision).where(DocumentRevision.document_id == document_id)
    )
    assert revision.index_status == "vector_pending" and revision.attempts == 1
    revision.retry_at = utcnow() - timedelta(seconds=1)
    db.commit()
    worker.gateway = EmbeddingGateway()
    assert asyncio.run(worker.run_once())
    db.expire_all()
    revision = db.get(DocumentRevision, revision.id)
    assert revision.index_status == "ready" and revision.indexed_count == revision.chunk_count
    assert (
        len(
            db.scalar(
                select(KnowledgeChunk).where(KnowledgeChunk.revision_id == revision.id)
            ).embedding
        )
        == 64
    )


def test_summary_every_eight_turns_and_revoked_lineage_is_omitted(db, settings):
    user, _, _ = seed(db)
    note = Note(owner_id=user.id, title="Private fact", body="Original", kind="thought")
    conversation = Conversation(owner_id=user.id, title="History")
    db.add_all([note, conversation])
    db.flush()
    refs = [
        {"type": "note", "id": note.id, "version": note.updated_at.isoformat(), "title": note.title}
    ]
    for i in range(8):
        db.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=f"Question {i}",
                created_at=utcnow() + timedelta(seconds=i * 2),
            )
        )
        db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=f"Answer {i}",
                source_refs=refs,
                created_at=utcnow() + timedelta(seconds=i * 2 + 1),
            )
        )
    user_id, conversation_id, note_id = user.id, conversation.id, note.id
    db.commit()

    class SummaryGateway:
        async def complete(self, messages, model=None):
            return Completion("SUMMARIZED_PRIVATE_CONTEXT", "test/model", {}, "request")

    service = ContextService(db, settings)
    assert asyncio.run(service.summarize_if_due(user, conversation, SummaryGateway())) == 8
    assert asyncio.run(service.summarize_if_due(user, conversation, SummaryGateway())) is None
    assert (
        "SUMMARIZED_PRIVATE_CONTEXT"
        in service.for_conversation(user, conversation, "Continue").messages[0]["content"]
    )
    db.delete(db.get(Note, note_id))
    db.flush()
    assert (
        "SUMMARIZED_PRIVATE_CONTEXT"
        not in service.for_conversation(user, conversation, "Continue").messages[0]["content"]
    )


def test_legacy_backfill_keeps_original_unavailable_and_does_not_invent_revision_content(
    db, settings
):
    user, _, workspace = seed(db)
    document = Document(
        workspace_id=workspace.id,
        title="Legacy",
        filename="source.txt",
        media_type="text/plain",
        content_hash="c" * 64,
        body="Retained extracted instruction",
        created_by=user.id,
    )
    db.add(document)
    db.flush()
    db.add(DocumentChunk(document_id=document.id, ordinal=0, body=document.body))
    db.flush()
    version = document.updated_at
    ingestion = IngestionService(db, settings)
    revision = ingestion.retry(user, document.id)
    assert revision.original_bytes is None and revision.original_size == 0
    assert revision.extracted_text == document.body and revision.index_status == "vector_pending"
    assert document.updated_at == version
    assert (
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
        )
        == 0
    )
    assert (
        RetrievalService(db, settings).search(user, "Retained", workspace.id)[0][
            "document_revision"
        ]
        == 1
    )


def test_preference_timezone_change_reschedules_existing_daily_checkin(db, settings):
    from app.models_personal import AssistantSettings
    from app.services.personal_routines import next_local_time

    user, _, _ = seed(db)
    automation = AssistantSettings(
        owner_id=user.id,
        daily_checkin_enabled=True,
        daily_checkin_time="09:00",
        next_checkin_at=utcnow() + timedelta(minutes=1),
    )
    db.add(automation)
    db.flush()
    previous = automation.next_checkin_at
    PreferenceService(db).update(user, {"timezone": "Pacific/Auckland"})
    assert automation.next_checkin_at != previous
    assert automation.next_checkin_at == next_local_time(utcnow(), "Pacific/Auckland", "09:00")


def test_context_versions_are_canonical_across_database_session_timezones(db, settings):
    from sqlalchemy import text
    from app.models import Project

    user, _, workspace = seed(db)
    PreferenceService(db).update(user, {"language": "English"})
    project = Project(owner_id=user.id, name="Diagnostic work", goal="Keep evidence")
    db.add(project)
    db.flush()
    service = ContextService(db, settings)
    _, shared_metadata = service.configuration(user, workspace.id)
    _, personal_metadata = service.configuration(user, project_id=project.id)
    user_id, workspace_id, project_id = user.id, workspace.id, project.id
    db.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
    db.expire_all()
    user = db.get(User, user_id)
    assert service.validate_metadata(user, shared_metadata, workspace_id)
    assert service.validate_metadata(user, personal_metadata, project_id=project_id)
