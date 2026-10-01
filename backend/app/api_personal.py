"""Authenticated personal capture, filing, settings and action review endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from .auth import Actor, current_actor
from .models_personal import (
    DailyCheckin,
    NotePlacement,
    OrganizationChange,
    PersonalAction,
    PersonalCapture,
    PersonalTask,
    ProjectFolder,
)
from .repositories.personal_assistant import PersonalAssistantRepository, record
from .schemas import Id
from .services.actions import ActionService
from .services.capture import CaptureService, capture_zone
from .services.organization import OrganizationService
from .services.personal_routines import PersonalRoutineService

router = APIRouter(prefix="/api/personal", tags=["Personal assistant"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptureCreate(Strict):
    text: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=8, max_length=128)
    project_id: Id | None = None
    timezone: str | None = Field(default=None, max_length=100)
    due_at: AwareDatetime | None = None
    classify: bool = True
    external_ai_consent: bool = False


class ClassifyRequest(Strict):
    external_ai_consent: bool = False


class CaptureApply(Strict):
    due_at: AwareDatetime | None = None
    project_id: Id | None = None
    create_memory: bool = False


class SettingsUpdate(Strict):
    timezone: str | None = None
    reminder_mode: Literal["off", "suggest", "automatic"] | None = None
    memory_mode: Literal["off", "suggest", "automatic"] | None = None
    organization_mode: Literal["off", "suggest", "automatic"] | None = None
    daily_checkin_enabled: bool | None = None
    daily_checkin_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    reminder_hour: int | None = Field(default=None, ge=0, le=23)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            capture_zone(value)
        return value


class FolderCreate(Strict):
    project_id: Id
    parent_id: Id | None = None
    name: str = Field(min_length=1, max_length=120)


class OrganizationPreview(Strict):
    project_id: Id | None = None


class CheckinComplete(Strict):
    text: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=8, max_length=128)
    classify: bool = True
    external_ai_consent: bool = False


class TaskUpdate(Strict):
    status: Literal["done", "dismissed"]


@router.get("/settings")
def settings(actor: Actor = Depends(current_actor)):
    result = PersonalRoutineService(actor.db).settings_payload(actor.user)
    actor.db.commit()
    return result


@router.patch("/settings")
def update_settings(data: SettingsUpdate, actor: Actor = Depends(current_actor)):
    service = PersonalRoutineService(actor.db)
    service.update_settings(actor.user, data.model_dump(exclude_unset=True, exclude_none=True))
    actor.db.commit()
    return service.settings_payload(actor.user)


@router.post("/captures", status_code=201)
async def capture(data: CaptureCreate, actor: Actor = Depends(current_actor)):
    service = CaptureService(actor.db)
    obj = service.save(
        actor.user,
        text=data.text,
        request_id=data.request_id,
        project_id=data.project_id,
        timezone_name=data.timezone,
        due_at=data.due_at,
    )
    actor.db.commit()
    if data.classify and data.external_ai_consent:
        obj = await service.classify(actor.user, obj.id, external_ai_consent=True)
        actor.db.commit()
    return service.payload(actor.user, obj)


@router.get("/captures")
def captures(limit: int = Query(default=50, ge=1, le=200), actor: Actor = Depends(current_actor)):
    service = CaptureService(actor.db)
    return [
        service.payload(actor.user, obj)
        for obj in actor.db.scalars(
            select(PersonalCapture)
            .where(PersonalCapture.owner_id == actor.user.id)
            .order_by(PersonalCapture.created_at.desc())
            .limit(limit)
        )
    ]


@router.get("/captures/{rid}")
def get_capture(rid: Id, actor: Actor = Depends(current_actor)):
    return CaptureService(actor.db).payload(
        actor.user, PersonalAssistantRepository(actor.db).owned(PersonalCapture, actor.user, rid)
    )


@router.post("/captures/{rid}/classify")
async def classify(rid: Id, data: ClassifyRequest, actor: Actor = Depends(current_actor)):
    service = CaptureService(actor.db)
    obj = await service.classify(actor.user, rid, external_ai_consent=data.external_ai_consent)
    actor.db.commit()
    return service.payload(actor.user, obj)


@router.post("/captures/{rid}/apply")
def apply_capture(rid: Id, data: CaptureApply, actor: Actor = Depends(current_actor)):
    service = CaptureService(actor.db)
    obj = service.apply(actor.user, rid, **data.model_dump())
    actor.db.commit()
    return service.payload(actor.user, obj)


@router.get("/tasks")
def tasks(actor: Actor = Depends(current_actor)):
    return [
        record(obj)
        for obj in actor.db.scalars(
            select(PersonalTask)
            .where(PersonalTask.owner_id == actor.user.id)
            .order_by(PersonalTask.created_at.desc())
            .limit(200)
        )
    ]


@router.patch("/tasks/{rid}")
def update_task(rid: Id, data: TaskUpdate, actor: Actor = Depends(current_actor)):
    repo = PersonalAssistantRepository(actor.db)
    repo.lock_owner(actor.user)
    task = repo.owned(PersonalTask, actor.user, rid, lock=True)
    task.status = data.status
    if task.reminder_id:
        from .models import Reminder

        reminder = actor.db.get(Reminder, task.reminder_id)
        if reminder and reminder.owner_id == actor.user.id:
            reminder.status = "done" if data.status == "done" else "cancelled"
    actor.db.commit()
    return record(task)


@router.get("/folders")
def folders(project_id: Id | None = None, actor: Actor = Depends(current_actor)):
    query = select(ProjectFolder).where(ProjectFolder.owner_id == actor.user.id)
    if project_id:
        PersonalAssistantRepository(actor.db).access.project(actor.user, project_id)
        query = query.where(ProjectFolder.project_id == project_id)
    return [record(obj) for obj in actor.db.scalars(query.order_by(ProjectFolder.name).limit(500))]


@router.post("/folders", status_code=201)
def create_folder(data: FolderCreate, actor: Actor = Depends(current_actor)):
    folder = OrganizationService(actor.db).create_folder(actor.user, **data.model_dump())
    actor.db.commit()
    return record(folder)


@router.get("/placements")
def placements(actor: Actor = Depends(current_actor)):
    return [
        record(obj)
        for obj in actor.db.scalars(
            select(NotePlacement).where(NotePlacement.owner_id == actor.user.id).limit(1000)
        )
    ]


@router.get("/actions")
def actions(limit: int = Query(default=100, ge=1, le=200), actor: Actor = Depends(current_actor)):
    return [
        record(obj)
        for obj in actor.db.scalars(
            select(PersonalAction)
            .where(PersonalAction.owner_id == actor.user.id)
            .order_by(PersonalAction.created_at.desc())
            .limit(limit)
        )
    ]


@router.post("/actions/{rid}/undo")
def undo(rid: Id, actor: Actor = Depends(current_actor)):
    action = ActionService(actor.db).undo(actor.user, rid)
    actor.db.commit()
    return record(action)


@router.get("/organization")
def organization(actor: Actor = Depends(current_actor)):
    return [
        record(obj)
        for obj in actor.db.scalars(
            select(OrganizationChange)
            .where(OrganizationChange.owner_id == actor.user.id)
            .order_by(OrganizationChange.created_at.desc())
            .limit(50)
        )
    ]


@router.post("/organization/preview")
def organization_preview(data: OrganizationPreview, actor: Actor = Depends(current_actor)):
    change = OrganizationService(actor.db).preview(actor.user, project_id=data.project_id)
    actor.db.commit()
    return record(change)


@router.post("/organization/{rid}/apply")
def organization_apply(rid: Id, actor: Actor = Depends(current_actor)):
    change = OrganizationService(actor.db).apply(actor.user, rid)
    actor.db.commit()
    return record(change)


@router.post("/organization/{rid}/undo")
def organization_undo(rid: Id, actor: Actor = Depends(current_actor)):
    change = OrganizationService(actor.db).undo(actor.user, rid)
    actor.db.commit()
    return record(change)


@router.get("/checkins")
def checkins(actor: Actor = Depends(current_actor)):
    return [
        record(obj)
        for obj in actor.db.scalars(
            select(DailyCheckin)
            .where(DailyCheckin.owner_id == actor.user.id)
            .order_by(DailyCheckin.local_date.desc())
            .limit(30)
        )
    ]


@router.post("/checkins", status_code=201)
def start_checkin(actor: Actor = Depends(current_actor)):
    checkin = PersonalRoutineService(actor.db).start_checkin(actor.user)
    actor.db.commit()
    return record(checkin)


@router.post("/checkins/{rid}/complete")
async def complete_checkin(rid: Id, data: CheckinComplete, actor: Actor = Depends(current_actor)):
    checkin = PersonalRoutineService(actor.db).complete_checkin(
        actor.user, rid, text=data.text, request_id=data.request_id
    )
    actor.db.commit()
    service = CaptureService(actor.db)
    if data.classify and data.external_ai_consent:
        await service.classify(actor.user, checkin.capture_id, external_ai_consent=True)
        actor.db.commit()
    obj = PersonalAssistantRepository(actor.db).owned(
        PersonalCapture, actor.user, checkin.capture_id
    )
    return {"checkin": record(checkin), **service.payload(actor.user, obj)}
