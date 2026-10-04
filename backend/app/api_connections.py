"""Authenticated connection setup, read-only resources and delayed delivery APIs."""

from datetime import datetime
from pathlib import PurePath
from typing import Any, Literal
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth import Actor, current_actor
from app.config import get_settings
from app.db import get_session
from app.schemas import Id
from app.models_connections import Delivery
from app.services.connections import ConnectionService
from app.services.calendar_sync import CalendarSyncService
from app.services.delivery import DeliveryService
from app.connectors.base import ConnectorHTTP, validate_url
from app.services.errors import ServiceError

router = APIRouter()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionCreate(Strict):
    provider: Literal[
        "influxdb", "zoom", "teams_workflow", "slack_webhook", "postgres", "gdrive_folder"
    ]
    name: str = Field(min_length=1, max_length=200)
    workspace_id: Id | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class ConnectionUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None


class MicrosoftStart(Strict):
    include_transcripts: bool = False


class DriveImport(Strict):
    file_id: str = Field(min_length=1, max_length=200)
    workspace_id: Id | None = None
    project_id: Id | None = None


class SeriesQuery(Strict):
    bucket_id: str = Field(min_length=1, max_length=200)
    measurement: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=200)
    start: datetime
    stop: datetime
    limit: int = Field(default=1000, ge=1, le=5000)
    aggregate_minutes: int | None = Field(default=None, ge=1, le=1440)
    aggregation: Literal["mean", "sum", "last"] = "mean"
    tags: dict[str, str] = Field(default_factory=dict)


class DeliveryCreate(Strict):
    channel: Literal["email", "teams", "slack"]
    recipient: str = Field(default="", max_length=320)
    connection_id: Id | None = None
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100000)
    request_id: str = Field(min_length=8, max_length=128)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=256)


def delivery_public(obj):
    return {
        key: getattr(obj, key)
        for key in (
            "id",
            "channel",
            "recipient",
            "subject",
            "body",
            "status",
            "undo_until",
            "available_at",
            "attempts",
            "last_error",
            "sent_at",
            "created_at",
        )
    }


@router.get("/api/connections/capabilities")
def capabilities(actor: Actor = Depends(current_actor)):
    settings = get_settings()
    return {
        "microsoft": {
            "configured": bool(
                settings.microsoft_client_id
                and settings.microsoft_client_secret
                and settings.credential_encryption_key
            ),
            "callback_url": settings.app_origin + "/api/connections/microsoft/callback",
            "requires": "Entra application registration and delegated consent; transcript permission may need administrator consent.",
        },
        "google": {
            "configured": bool(
                settings.google_client_id
                and settings.google_client_secret
                and settings.credential_encryption_key
            ),
            "callback_url": settings.app_origin + "/api/connections/google/callback",
        },
        "speech": {"configured": bool(settings.speech_api_key and settings.allow_external_ai)},
        "email": {"configured": bool(settings.smtp_host and settings.smtp_from)},
        "encrypted_connections": bool(settings.credential_encryption_key),
        "providers": [
            "google",
            "microsoft",
            "zoom",
            "influxdb",
            "postgres",
            "gdrive_folder",
            "teams_workflow",
            "slack_webhook",
        ],
    }


@router.get("/api/connections")
def connections(workspace_id: Id | None = None, actor: Actor = Depends(current_actor)):
    return ConnectionService(actor.db).list(actor.user, workspace_id)


@router.post("/api/connections", status_code=201)
def create_connection(data: ConnectionCreate, actor: Actor = Depends(current_actor)):
    service = ConnectionService(actor.db)
    obj = service.create(actor.user, **data.model_dump())
    actor.db.commit()
    return service.public(obj)


@router.patch("/api/connections/{rid}")
def update_connection(rid: Id, data: ConnectionUpdate, actor: Actor = Depends(current_actor)):
    service = ConnectionService(actor.db)
    obj = service.update(actor.user, rid, **data.model_dump())
    actor.db.commit()
    return service.public(obj)


@router.delete("/api/connections/{rid}")
def disconnect(rid: Id, actor: Actor = Depends(current_actor)):
    service = ConnectionService(actor.db)
    obj = service.disconnect(actor.user, rid)
    actor.db.commit()
    return service.public(obj)


@router.post("/api/connections/microsoft/start")
def microsoft_start(data: MicrosoftStart, actor: Actor = Depends(current_actor)):
    result = ConnectionService(actor.db).start_microsoft(actor.user, **data.model_dump())
    actor.db.commit()
    return result


@router.get("/api/connections/microsoft/callback")
async def microsoft_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_session),
):
    # Random, hashed, one-use, expiring state binds this callback to the initiating
    # account; no ambient session cookie is required after a cross-site redirect.
    if len(state) > 500 or code and len(code) > 10000:
        raise ServiceError("Invalid authorization callback.", 400)
    obj = await ConnectionService(db).finish_microsoft(state=state, code=code, error=error)
    return RedirectResponse(
        get_settings().app_origin
        + "/?connection="
        + ("connected" if obj.status == "connected" else "error"),
        status_code=303,
    )


@router.post("/api/connections/google/start")
def google_start(actor: Actor = Depends(current_actor)):
    result = ConnectionService(actor.db).start_google(actor.user)
    actor.db.commit()
    return result


@router.get("/api/connections/google/callback")
async def google_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_session),
):
    # Same one-use hashed state contract as the Microsoft callback.
    if len(state) > 500 or code and len(code) > 10000:
        raise ServiceError("Invalid authorization callback.", 400)
    obj = await ConnectionService(db).finish_google(state=state, code=code, error=error)
    return RedirectResponse(
        get_settings().app_origin
        + "/?connection="
        + ("connected" if obj.status == "connected" else "error"),
        status_code=303,
    )


@router.get("/api/connections/{rid}/drive/files")
async def drive_files(rid: Id, q: str = "", actor: Actor = Depends(current_actor)):
    service = ConnectionService(actor.db)
    files = await service.drive_files(actor.user, rid, q)
    actor.db.commit()  # Persist a refreshed access token.
    return files


@router.post("/api/connections/{rid}/drive/import", status_code=201)
async def drive_import(rid: Id, data: DriveImport, actor: Actor = Depends(current_actor)):
    service = ConnectionService(actor.db)
    document = await service.import_drive_file(actor.user, rid, **data.model_dump())
    actor.db.commit()
    return {"id": document.id, "title": document.title}


@router.get("/api/connections/{rid}/resources")
async def resources(rid: Id, actor: Actor = Depends(current_actor)):
    service = ConnectionService(actor.db)
    try:
        result = await service.resources(actor.user, rid)
    except ServiceError as exc:
        obj = service.repo.get(actor.user, rid, manage=True)
        obj.status = "error"
        obj.last_error = exc.detail[:500]
        actor.db.commit()
        raise
    actor.db.commit()
    return result


@router.post("/api/connections/{rid}/sync")
async def sync(rid: Id, actor: Actor = Depends(current_actor)):
    result = await CalendarSyncService(actor.db).sync(actor.user, rid)
    actor.db.commit()
    return result


@router.post("/api/connections/{rid}/series")
async def series(rid: Id, data: SeriesQuery, actor: Actor = Depends(current_actor)):
    result = await ConnectionService(actor.db).query_series(actor.user, rid, **data.model_dump())
    return {"rows": result, "count": len(result), "truncated": False}


@router.get("/api/calendar/events")
def calendar_events(start: datetime, end: datetime, actor: Actor = Depends(current_actor)):
    return CalendarSyncService(actor.db).events(actor.user, start, end)


@router.post("/api/speech/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    external_ai_consent: bool = Form(False),
    actor: Actor = Depends(current_actor),
):
    settings = get_settings()
    if not settings.allow_external_ai or not external_ai_consent:
        raise ServiceError("Approve external transcription before uploading audio.", 403)
    if not settings.speech_api_key:
        raise ServiceError("Voice transcription is not configured.", 503)
    filename = PurePath(file.filename or "recording.webm").name
    if PurePath(filename).suffix.lower() not in {
        ".flac",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpga",
        ".m4a",
        ".ogg",
        ".wav",
        ".webm",
    }:
        raise ServiceError("Use a supported audio recording format.", 415)
    data = await file.read(25_000_001)
    if not data or len(data) > 25_000_000:
        raise ServiceError("Audio files must be nonempty and below 25 MB.", 413)
    base = validate_url(settings.speech_base_url, allow_query=False)
    result = await ConnectorHTTP(timeout=120).json(
        "POST",
        base + "/audio/transcriptions",
        headers={"Authorization": "Bearer " + settings.speech_api_key},
        files={"file": (filename, data, file.content_type or "application/octet-stream")},
        data={"model": settings.speech_model, "response_format": "json"},
    )
    if not isinstance(result.get("text"), str) or not result["text"].strip():
        raise ServiceError("No speech was recognized. Please try again.", 422)
    return {"text": result["text"].strip()}


@router.get("/api/deliveries")
def deliveries(actor: Actor = Depends(current_actor)):
    return [
        delivery_public(obj)
        for obj in actor.db.scalars(
            select(Delivery)
            .where(Delivery.owner_id == actor.user.id)
            .order_by(Delivery.created_at.desc())
            .limit(100)
        )
    ]


@router.post("/api/deliveries", status_code=202)
def enqueue(data: DeliveryCreate, actor: Actor = Depends(current_actor)):
    obj = DeliveryService(actor.db).enqueue(actor.user, **data.model_dump())
    actor.db.commit()
    return delivery_public(obj)


@router.post("/api/deliveries/{rid}/undo")
def undo_delivery(rid: Id, actor: Actor = Depends(current_actor)):
    obj = DeliveryService(actor.db).cancel(actor.user, rid)
    actor.db.commit()
    return delivery_public(obj)
