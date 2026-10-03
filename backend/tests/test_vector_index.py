"""Model tiers and the Qdrant vector backend.

The live Qdrant test needs TEST_DATABASE_URL and TEST_QDRANT_URL (a disposable
Qdrant; each run uses and then drops its own collection).
"""

import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from app.config import Settings
from app.models_knowledge import DocumentRevision, KnowledgeChunk
from app.repositories import NotFound
from app.services.ingestion import IngestionService, KnowledgeIndexer
from app.services.retrieval import RetrievalService
from app.services.vector_index import QdrantIndex, audience_key
from test_knowledge import EmbeddingGateway, seed

TIERS = {
    "openrouter_model_fast": "vendor/fast",
    "openrouter_model_medium": "vendor/medium",
    "openrouter_model_high": "vendor/high",
}


@pytest.fixture
def no_model_env(monkeypatch):
    for name in ("OPENROUTER_MODELS", "OPENROUTER_DEFAULT_MODEL", "OPENROUTER_SYSTEM1_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_model_levels_form_allowlist_default_and_fast_path(no_model_env):
    settings = Settings(_env_file=None, **TIERS)
    assert settings.allowed_models == ["vendor/fast", "vendor/medium", "vendor/high"]
    assert settings.openrouter_default_model == "vendor/medium"
    assert settings.openrouter_system1_model == "vendor/fast"
    assert settings.model_choices == [
        {"id": "fast", "label": "Fast"},
        {"id": "medium", "label": "Medium Reasoning"},
        {"id": "high", "label": "High"},
    ]


def test_clients_see_levels_and_never_model_names(no_model_env):
    from app.services.gateway import select_model

    settings = Settings(_env_file=None, openrouter_models="vendor/extra", **TIERS)
    assert select_model("high", settings) == "vendor/high"
    assert select_model(None, settings) == "vendor/medium"
    assert settings.public_model("vendor/fast") == "fast"
    assert settings.public_model("vendor/extra") is None  # allowed, but never named
    assert settings.public_model("deterministic") == "deterministic"
    # Extra allowlisted models are usable internally but never offered in menus.
    assert [c["id"] for c in settings.model_choices] == ["fast", "medium", "high"]


def test_explicit_model_settings_survive_levels(no_model_env):
    settings = Settings(
        _env_file=None,
        openrouter_models="vendor/extra",
        openrouter_default_model="vendor/high",
        openrouter_system1_model="vendor/extra",
        **TIERS,
    )
    assert settings.allowed_models[-1] == "vendor/extra"
    assert settings.openrouter_default_model == "vendor/high"
    assert settings.openrouter_system1_model == "vendor/extra"


def test_without_levels_the_default_model_is_medium(no_model_env):
    settings = Settings(
        _env_file=None, openrouter_models="a/one,a/two", openrouter_default_model="a/two"
    )
    assert settings.allowed_models == ["a/one", "a/two"]
    assert settings.openrouter_system1_model == ""
    assert settings.model_choices == [{"id": "medium", "label": "Medium Reasoning"}]
    assert settings.resolve_model("medium") == "a/two" and settings.public_model("a/one") is None


def test_qdrant_requests_carry_audience_filter_profile_and_key():
    seen = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, request.headers.get("api-key")))
        if request.url.path.endswith("/points/query"):
            return httpx.Response(200, json={"result": {"points": [{"id": "c1"}, {"id": "c2"}]}})
        return httpx.Response(200, json={"result": True})

    settings = Settings(
        _env_file=None,
        qdrant_url="http://qdrant.test:6333/",
        qdrant_api_key="secret",
        qdrant_collection="plant",
        embedding_dimensions=64,
    )
    index = QdrantIndex(settings, transport=httpx.MockTransport(handler))
    ids = asyncio.run(
        index.search([0.1] * 64, audience=audience_key(None, "u1"), project_id="p1", limit=5)
    )
    assert ids == ["c1", "c2"]
    method, path, body, key = seen[-1]
    assert (method, path, key) == ("POST", "/collections/plant_64/points/query", "secret")
    assert {"key": "audience", "match": {"value": "user:u1"}} in body["filter"]["must"]
    assert {"key": "project_id", "match": {"value": "p1"}} in body["filter"]["must"]
    assert body["limit"] == 5 and body["with_payload"] is False


def test_qdrant_search_before_any_indexing_is_empty_and_outage_is_provider_error():
    settings = Settings(_env_file=None, embedding_dimensions=64)
    missing = QdrantIndex(settings, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert (
        asyncio.run(
            missing.search([1.0] + [0.0] * 63, audience="workspace:w", project_id=None, limit=3)
        )
        == []
    )

    def offline(request):
        raise httpx.ConnectError("refused")

    from app.services.errors import ProviderError

    down = QdrantIndex(settings, transport=httpx.MockTransport(offline))
    with pytest.raises(ProviderError):
        asyncio.run(
            down.search([1.0] + [0.0] * 63, audience="workspace:w", project_id=None, limit=3)
        )


@pytest.fixture
def qdrant_settings():
    url = os.environ.get("TEST_QDRANT_URL")
    if not url:
        pytest.skip("Set TEST_QDRANT_URL to a disposable Qdrant instance")
    settings = Settings(
        allow_external_ai=True,
        openrouter_api_key="test",
        openrouter_models="test/model",
        openrouter_default_model="test/model",
        embeddings_enabled=True,
        embedding_dimensions=64,
        embedding_batch_size=8,
        vector_backend="qdrant",
        qdrant_url=url,
        qdrant_collection=f"test_{uuid4().hex[:10]}",
    )
    yield settings
    index = QdrantIndex(settings)
    with index._client(sync=True) as client:
        client.delete(f"/collections/{index.collection}")


def test_live_qdrant_indexes_retrieves_and_keeps_audience_boundaries(
    db, db_factory, qdrant_settings
):
    settings = qdrant_settings
    user, other, workspace = seed(db)
    ingestion = IngestionService(db, settings)
    shared = ingestion.ingest(
        user, b"blast furnace tuyere cooling SOP", "sop.txt", workspace_id=workspace.id
    )
    private = ingestion.ingest(other, b"PRIVATE_MARKER furnace notes", "mine.txt")
    shared_id, private_id, workspace_id = shared.id, private.id, workspace.id
    db.commit()
    indexer = KnowledgeIndexer(db_factory, settings, EmbeddingGateway())
    while asyncio.run(indexer.run_once()):
        pass
    db.expire_all()
    revisions = db.scalars(select(DocumentRevision)).all()
    assert revisions and all(revision.index_status == "ready" for revision in revisions)

    retrieval = RetrievalService(db, settings)
    results = asyncio.run(
        retrieval.search_hybrid(
            user, "an unrelated paraphrase", workspace_id, gateway=EmbeddingGateway()
        )
    )
    assert results[0]["id"] == shared_id and results[0]["retrieval"] == "semantic"
    assert private_id not in {row["id"] for row in results}
    assert retrieval.last_status["mode"] == "hybrid"
    with pytest.raises(NotFound):
        asyncio.run(
            RetrievalService(db, settings).search_hybrid(
                other, "furnace", workspace_id, gateway=EmbeddingGateway()
            )
        )

    # A new revision replaces the old revision's points once it is indexed.
    ingestion = IngestionService(db, settings)
    ingestion.ingest(user, b"revised tuyere cooling SOP", "sop.txt", document_id=shared_id)
    db.commit()
    while asyncio.run(indexer.run_once()):
        pass
    current = db.scalar(
        select(DocumentRevision).where(
            DocumentRevision.document_id == shared_id, DocumentRevision.is_current.is_(True)
        )
    )
    current_chunks = {
        str(c)
        for c in db.scalars(
            select(KnowledgeChunk.id).where(KnowledgeChunk.revision_id == current.id)
        )
    }
    candidates = asyncio.run(
        QdrantIndex(settings).search(
            [1.0] + [0.0] * 63, audience=audience_key(workspace_id, None), project_id=None, limit=50
        )
    )
    assert set(candidates) == current_chunks
