"""Authenticated workspace context, preferences and knowledge lifecycle endpoints."""

from typing import Any, Literal
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.auth import Actor, current_actor
from app.config import get_settings
from app.models_knowledge import DocumentRevision, WorkspaceBrief
from app.repositories import AccessRepository, NotFound
from app.repositories.knowledge import KnowledgeRepository
from app.schemas import Id
from app.services.context import ContextService
from app.services.errors import ServiceError
from app.services.gateway import OpenRouterGateway
from app.services.ingestion import IngestionService
from app.services.preferences import DEFAULTS, PreferenceService
from app.services.workspace_briefs import WorkspaceBriefService

router = APIRouter()


class BriefDraft(BaseModel):
    body: str = Field(min_length=1, max_length=12000)
    details: dict[str, Any] = Field(default_factory=dict)


class GenerateBrief(BaseModel):
    instructions: str = Field(default="", max_length=4000)
    model: str | None = None


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default=None, min_length=1, max_length=80)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    units: Literal["SI", "Imperial", "source"] | None = None
    response_style: Literal["concise", "detailed", "technical", "plain_language"] | None = None
    personal_instructions: str | None = Field(default=None, max_length=3000)


def brief_payload(row):
    if row is None:
        return None
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "workspace_id",
            "revision",
            "status",
            "body",
            "details",
            "created_by",
            "created_at",
            "published_at",
        )
    }


def index_payload(revision):
    if revision is None:
        return {
            "index_status": "legacy",
            "original_available": False,
            "detail": "Retry indexing to upgrade retained text. Upload a revision to retain its original file.",
        }
    return {
        **{
            key: getattr(revision, key)
            for key in (
                "id",
                "revision",
                "is_current",
                "content_hash",
                "filename",
                "media_type",
                "index_status",
                "chunk_count",
                "indexed_count",
                "error",
                "embedding_model",
                "embedding_dimensions",
                "created_at",
            )
        },
        "original_available": revision.original_size > 0,
        "lexical_available": True,
    }


@router.get("/api/preferences")
def preferences(actor: Actor = Depends(current_actor)):
    return PreferenceService(actor.db).get(actor.user)


@router.patch("/api/preferences")
def update_preferences(data: PreferenceUpdate, actor: Actor = Depends(current_actor)):
    changes = data.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise ServiceError("Preference fields cannot be null.", 422)
    result = PreferenceService(actor.db).update(actor.user, changes)
    actor.db.commit()
    return result


@router.get("/api/workspaces/{rid}/brief")
def get_brief(rid: Id, actor: Actor = Depends(current_actor)):
    workspace = AccessRepository(actor.db).workspace(actor.user, rid)
    rows = list(
        actor.db.scalars(
            select(WorkspaceBrief)
            .where(WorkspaceBrief.workspace_id == rid)
            .order_by(WorkspaceBrief.revision.desc())
            .limit(50)
        )
    )
    return {
        "published": next((brief_payload(row) for row in rows if row.status == "published"), None),
        "drafts": [brief_payload(row) for row in rows if row.status == "draft"],
        "history": [brief_payload(row) for row in rows if row.status == "superseded"],
        "fallback_purpose": workspace.description,
    }


@router.post("/api/workspaces/{rid}/brief", status_code=201)
def draft_brief(rid: Id, data: BriefDraft, actor: Actor = Depends(current_actor)):
    result = WorkspaceBriefService(actor.db).create_draft(actor.user, rid, data.body, data.details)
    actor.db.commit()
    return brief_payload(result)


@router.post("/api/workspaces/{rid}/brief/generate", status_code=201)
async def generate_brief(rid: Id, data: GenerateBrief, actor: Actor = Depends(current_actor)):
    result = await WorkspaceBriefService(actor.db).generate(
        actor.user, rid, OpenRouterGateway(), data.instructions, data.model
    )
    actor.db.commit()
    return brief_payload(result)


@router.post("/api/workspaces/{rid}/brief/{brief_id}/publish")
def publish_brief(rid: Id, brief_id: Id, actor: Actor = Depends(current_actor)):
    result = WorkspaceBriefService(actor.db).publish(actor.user, rid, brief_id)
    actor.db.commit()
    return brief_payload(result)


@router.get("/api/conversations/{rid}/context")
def context_preview(rid: Id, query: str = "", actor: Actor = Depends(current_actor)):
    conversation = AccessRepository(actor.db).conversation(
        actor.user, rid, roles={"member", "manager"}
    )
    bundle = ContextService(actor.db).for_conversation(
        actor.user, conversation, query[:8000] or "Describe the available context"
    )
    return {
        "metadata": bundle.metadata,
        "sources": bundle.sources,
        "chars": sum(len(message["content"]) for message in bundle.messages),
        "context_preview": bundle.messages[0]["content"],
        "retrieval": "lexical preview; authorized semantic search is available through the agent knowledge tool",
    }


@router.get("/api/documents/{rid}/index")
def document_index(rid: Id, actor: Actor = Depends(current_actor)):
    return index_payload(KnowledgeRepository(actor.db).current_revision(actor.user, rid))


@router.post("/api/documents/{rid}/index/retry", status_code=202)
def retry_index(rid: Id, actor: Actor = Depends(current_actor)):
    revision = IngestionService(actor.db).retry(actor.user, rid)
    actor.db.commit()
    return index_payload(revision)


@router.get("/api/documents/{rid}/revisions")
def document_revisions(rid: Id, actor: Actor = Depends(current_actor)):
    AccessRepository(actor.db).document(actor.user, rid)
    return [
        index_payload(row)
        for row in actor.db.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.document_id == rid)
            .order_by(DocumentRevision.revision.desc())
            .limit(100)
        )
    ]


@router.post("/api/documents/{rid}/revisions", status_code=201)
def upload_revision(rid: Id, file: UploadFile = File(...), actor: Actor = Depends(current_actor)):
    data = file.file.read(get_settings().max_upload_bytes + 1)
    document = IngestionService(actor.db).ingest(
        actor.user, data, file.filename or "document.txt", document_id=rid
    )
    actor.db.commit()
    return index_payload(KnowledgeRepository(actor.db).current_revision(actor.user, document.id))


@router.get("/api/documents/{rid}/original")
def download_original(rid: Id, actor: Actor = Depends(current_actor)):
    revision = KnowledgeRepository(actor.db).current_revision(actor.user, rid)
    if revision is None or revision.original_bytes is None:
        raise NotFound("Original document is unavailable")
    from urllib.parse import quote

    return Response(
        content=revision.original_bytes,
        media_type=revision.media_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(revision.filename, safe="")
        },
    )
