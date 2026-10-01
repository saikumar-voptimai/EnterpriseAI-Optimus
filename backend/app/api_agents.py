"""Durable agent API: start, inspect, cancel and preview authorized context."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.auth import Actor, current_actor
from app.models_agent import AgentRun
from app.services.agent_runs import AgentRunService
from app.services.context import ContextService
from app.agents.catalog import SkillCatalog

router = APIRouter(prefix="/api", tags=["agents"])


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=12000)
    model: str | None = Field(default=None, max_length=200)
    request_id: UUID
    external_ai_consent: bool = False


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(default="Summarize the current context", min_length=1, max_length=12000)


@router.post("/conversations/{conversation_id}/runs", status_code=202)
def create_run(conversation_id: UUID, data: RunCreate, actor: Actor = Depends(current_actor)):
    if not data.external_ai_consent:
        raise HTTPException(
            403,
            "Confirm that the message and authorized context may be sent to the configured AI provider.",
        )
    service = AgentRunService(actor.db)
    run = service.enqueue_chat(
        actor.user, str(conversation_id), data.content, data.model, str(data.request_id)
    )
    actor.db.commit()
    return service.serialize(actor.user, run)


@router.get("/agent-runs/{run_id}")
def get_run(run_id: UUID, actor: Actor = Depends(current_actor)):
    service = AgentRunService(actor.db)
    return service.serialize(actor.user, service.get(actor.user, str(run_id)))


@router.post("/agent-runs/{run_id}/cancel")
def cancel_run(run_id: UUID, actor: Actor = Depends(current_actor)):
    service = AgentRunService(actor.db)
    run = service.cancel(actor.user, str(run_id))
    actor.db.commit()
    return service.serialize(actor.user, run)


@router.get("/conversations/{conversation_id}/runs")
def list_runs(conversation_id: UUID, actor: Actor = Depends(current_actor)):
    service = AgentRunService(actor.db)
    service.access.conversation(actor.user, str(conversation_id))
    runs = actor.db.scalars(
        select(AgentRun)
        .where(AgentRun.owner_id == actor.user.id, AgentRun.conversation_id == str(conversation_id))
        .order_by(AgentRun.created_at.desc())
        .limit(30)
    ).all()
    # Reauthorize each persisted result before exposing any model-generated content.
    result = []
    from app.repositories import NotFound

    for run in runs:
        try:
            result.append(service.serialize(actor.user, run, include_events=False))
        except NotFound:
            continue
    return result


@router.post("/conversations/{conversation_id}/context-preview")
def preview(conversation_id: UUID, data: PreviewRequest, actor: Actor = Depends(current_actor)):
    service = AgentRunService(actor.db)
    conversation = service.authorize_conversation(actor.user, str(conversation_id))
    context = ContextService(actor.db, reserve_chars=4500).for_conversation(
        actor.user, conversation, data.content
    )
    skills = SkillCatalog().select(data.content, conversation.workspace_id)
    return {
        "sources": context.sources,
        "metadata": getattr(context, "metadata", {}),
        "skills": [{k: v for k, v in skill.items() if k != "instructions"} for skill in skills],
        "tools": [
            "knowledge_search",
            "list_incidents",
            "find_reports",
            "calculate_statistics",
            "list_data_connections",
            "read_timeseries",
        ]
        + (
            []
            if conversation.workspace_id
            else ["calendar_events", "save_personal_note", "create_personal_reminder"]
        ),
        "audience": "workspace" if conversation.workspace_id else "personal",
    }
