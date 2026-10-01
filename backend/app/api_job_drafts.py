"""AI-assisted schedule setup; this endpoint never creates or activates a job."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from app.auth import Actor, current_actor
from app.schemas import Id
from app.services.job_drafts import JobDraftService, TaskType

router = APIRouter(prefix="/api", tags=["Scheduled work"])


class JobDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: Id | None = None
    instructions: str = Field(min_length=1, max_length=12000)
    task_type: TaskType | None = None
    selected_metrics: list[dict] = Field(default_factory=list, max_length=20)
    external_ai_consent: bool = False


@router.post("/job-drafts")
async def draft_job(data: JobDraftRequest, actor: Actor = Depends(current_actor)):
    return await JobDraftService(actor.db).draft(actor.user, **data.model_dump())
