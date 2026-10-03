"""Meeting Room APIs: import evidence, draft, review, publish and accept personal changes."""

from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from app.auth import Actor, current_actor
from app.schemas import Id
from app.models import User
from app.api_connections import Strict
from app.services.meetings import MeetingService, Minutes
from app.services.connections import ConnectionService
from app.connectors.google import GoogleAdapter
from app.connectors.microsoft import MicrosoftAdapter
from app.connectors.zoom import ZoomAdapter
from app.services.errors import ServiceError

router = APIRouter()


class MeetingCreate(Strict):
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    attendee_ids: list[Id] = Field(default_factory=list, max_length=100)


class ArtifactCreate(Strict):
    kind: Literal["transcript", "minutes", "notes"]
    content: str = Field(min_length=1, max_length=500000)
    source_label: str = Field(
        default="Owner-supplied meeting artifact", min_length=1, max_length=300
    )


class DraftRequest(Strict):
    external_ai_consent: bool = False


class ReviewRequest(Minutes):
    workspace_ids: list[Id] = Field(default_factory=list, max_length=20)


class ProviderArtifactRequest(Strict):
    connection_id: Id
    meeting_id: str = Field(min_length=1, max_length=1024)
    artifact_id: str = Field(min_length=1, max_length=1024)


@router.get("/api/meetings/people")
def meeting_people(actor: Actor = Depends(current_actor)):
    return [
        {"id": u.id, "name": u.name, "email": u.email}
        for u in actor.db.scalars(
            select(User).where(User.active.is_(True)).order_by(User.name).limit(1000)
        )
    ]


@router.get("/api/meetings")
def meetings(actor: Actor = Depends(current_actor)):
    return MeetingService(actor.db).list(actor.user)


@router.post("/api/meetings", status_code=201)
def create(data: MeetingCreate, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    room = service.create(actor.user, **data.model_dump())
    actor.db.commit()
    return service.payload(actor.user, room)


@router.get("/api/meetings/{rid}")
def meeting(rid: Id, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    return service.payload(actor.user, service.repo.meeting(actor.user, rid))


@router.post("/api/meetings/{rid}/artifacts", status_code=201)
def import_artifact(rid: Id, data: ArtifactCreate, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    service.import_artifact(actor.user, rid, **data.model_dump())
    actor.db.commit()
    return service.payload(actor.user, service.repo.meeting(actor.user, rid))


@router.get("/api/connections/{connection_id}/meeting-artifacts")
async def discover_provider_artifacts(
    connection_id: Id, meeting_id: str, actor: Actor = Depends(current_actor)
):
    service = ConnectionService(actor.db)
    obj = service.repo.get(actor.user, connection_id, manage=True, lock=True)
    if len(meeting_id) > 1024:
        raise ServiceError("Invalid meeting identifier.", 422)
    if obj.provider == "microsoft":
        if not obj.config.get("include_transcripts"):
            raise ServiceError("Reconnect Microsoft with transcript permissions enabled.", 403)
        results = await MicrosoftAdapter(service.settings).transcripts(
            await service.microsoft_token(obj), meeting_id
        )
        artifacts = [
            {"id": r["id"], "kind": "transcript", "created_at": r.get("createdDateTime")}
            for r in results
        ]
    elif obj.provider == "google":
        artifacts = await GoogleAdapter(service.settings).meet_transcripts(
            await service.google_token(obj), meeting_id
        )
    elif obj.provider == "zoom":
        result, _ = await ZoomAdapter(service.credentials(obj)).recordings(meeting_id)
        artifacts = [
            {"id": r["id"], "kind": "transcript", "created_at": r.get("recording_start")}
            for r in result.get("recording_files", [])
            if r.get("recording_type") == "audio_transcript" and r.get("status") == "completed"
        ]
    else:
        raise ServiceError("Choose a Google, Microsoft or Zoom connection.", 422)
    actor.db.commit()
    return {"artifacts": artifacts}


@router.post("/api/meetings/{rid}/provider-artifacts", status_code=201)
async def provider_artifact(
    rid: Id, data: ProviderArtifactRequest, actor: Actor = Depends(current_actor)
):
    meetings = MeetingService(actor.db)
    meetings.repo.meeting(actor.user, rid, manage=True)
    service = ConnectionService(actor.db)
    obj = service.repo.get(actor.user, data.connection_id, manage=True, lock=True)
    if obj.provider == "microsoft":
        if not obj.config.get("include_transcripts"):
            raise ServiceError("Reconnect Microsoft with transcript permissions enabled.", 403)
        text = await MicrosoftAdapter(service.settings).transcript(
            await service.microsoft_token(obj), data.meeting_id, data.artifact_id
        )
    elif obj.provider == "google":
        text = await GoogleAdapter(service.settings).meet_transcript_text(
            await service.google_token(obj), data.artifact_id
        )
    elif obj.provider == "zoom":
        text = await ZoomAdapter(service.credentials(obj)).transcript(
            data.meeting_id, data.artifact_id
        )
    else:
        raise ServiceError("Choose a Google, Microsoft or Zoom connection.", 422)
    meetings.import_artifact(
        actor.user,
        rid,
        kind="transcript",
        content=text,
        source_label={"google": "Google Meet", "microsoft": "Teams", "zoom": "Zoom"}.get(
            obj.provider, "Meeting"
        )
        + " transcript",
        provider_reference={
            "provider": obj.provider,
            "connection_id": obj.id,
            "meeting_id": data.meeting_id,
            "artifact_id": data.artifact_id,
        },
    )
    actor.db.commit()
    return meetings.payload(actor.user, meetings.repo.meeting(actor.user, rid))


@router.post("/api/meetings/{rid}/draft", status_code=202)
async def draft(rid: Id, data: DraftRequest, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    room = await service.draft(actor.user, rid, **data.model_dump())
    actor.db.commit()
    return service.payload(actor.user, room)


@router.put("/api/meetings/{rid}/review")
def review(rid: Id, data: ReviewRequest, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    room = service.review(
        actor.user,
        rid,
        minutes=Minutes.model_validate(data.model_dump(exclude={"workspace_ids"})),
        workspace_ids=data.workspace_ids,
    )
    actor.db.commit()
    return service.payload(actor.user, room)


@router.post("/api/meetings/{rid}/publish")
def publish(rid: Id, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    room = service.publish(actor.user, rid)
    actor.db.commit()
    return service.payload(actor.user, room)


@router.post("/api/meetings/{rid}/accept")
def accept(rid: Id, actor: Actor = Depends(current_actor)):
    result = MeetingService(actor.db).accept(actor.user, rid)
    actor.db.commit()
    return result


@router.post("/api/meetings/{rid}/archive")
def archive(rid: Id, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    room = service.archive(actor.user, rid)
    actor.db.commit()
    return service.payload(actor.user, room)


@router.post("/api/meetings/{rid}/extraction/cancel")
def cancel_extraction(rid: Id, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    service.cancel_extraction(actor.user, rid)
    actor.db.commit()
    return service.payload(actor.user, service.repo.meeting(actor.user, rid))


@router.get("/api/meetings/{rid}/artifacts/{artifact_id}")
def artifact_content(rid: Id, artifact_id: Id, actor: Actor = Depends(current_actor)):
    service = MeetingService(actor.db)
    service.repo.meeting(actor.user, rid)
    from app.models_connections import MeetingArtifact

    artifact = actor.db.scalar(
        select(MeetingArtifact).where(
            MeetingArtifact.id == artifact_id, MeetingArtifact.room_id == rid
        )
    )
    if artifact is None:
        from app.repositories.access import NotFound

        raise NotFound("Meeting artifact not found")
    return {
        key: getattr(artifact, key)
        for key in ("id", "kind", "content", "source_label", "created_at")
    }
