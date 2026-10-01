"""Capture provenance, idempotency, reversible actions and personal scheduling."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import Memory, Note, Notification, Project, Reminder, User, utcnow
from app.models_personal import (
    NotePlacement,
    PersonalAction,
    PersonalCapture,
    PersonalTask,
    ProjectFolder,
)
from app.services.actions import ActionService
from app.services.capture import CaptureService, suggested_reminder_time
from app.services.errors import ProviderError, ServiceError
from app.services.gateway import Completion
from app.services.organization import OrganizationService
from app.services.personal_routines import PersonalRoutineService, next_local_time


def owner(db, name="owner"):
    user = User(name=name, email=f"{name}@personal.test", password_hash="test", active=True)
    db.add(user)
    db.flush()
    return user


def classification(**updates):
    return dict(
        labels=["thought", "task"],
        title="Analyse furnace readings",
        project_id=None,
        folder_name="Furnace analysis",
        memory_text=None,
        task_title="Analyse today's furnace data",
        requested_date_text=None,
        suggested_due_at=None,
        **updates,
    )


class StubGateway:
    def __init__(self, output=None, fail=False):
        self.output, self.fail, self.calls = output or classification(), fail, 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        assert kwargs["response_format"]["json_schema"]["strict"]
        if self.fail:
            raise ProviderError("Configured provider is temporarily unavailable.")
        return Completion(json.dumps(self.output), "test/model-a", {}, "test-classification")


def test_local_time_proposal_keeps_tomorrow_in_user_timezone():
    now = datetime(2026, 9, 27, 22, 30, tzinfo=timezone.utc)
    # It is already September 28 in India. Tomorrow means September 29 there.
    assert suggested_reminder_time(now, "Asia/Kolkata") == datetime(
        2026, 9, 29, 7, 30, tzinfo=timezone.utc
    )
    assert next_local_time(now, "Asia/Kolkata", "17:00") == datetime(
        2026, 9, 28, 11, 30, tzinfo=timezone.utc
    )


def test_capture_idempotency_and_undo_preserves_original(db):
    user = owner(db)
    service = CaptureService(db)
    payload = dict(
        text="Original thought about today's readings.",
        request_id=str(uuid4()),
        timezone_name="Asia/Kolkata",
    )
    first = service.save(user, **payload)
    second = service.save(user, **payload)
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(Note)) == 1
    with pytest.raises(ServiceError, match="already used"):
        service.save(user, **{**payload, "text": "Changed thought"})
    action = service.payload(user, first)["actions"][0]
    ActionService(db).undo(user, action["id"])
    db.flush()
    assert first.text == payload["text"] and first.status == "undone"
    assert first.note_id is None
    assert db.scalar(select(func.count()).select_from(Note)) == 0


def test_capture_intention_is_proposal_not_invented_deadline(db):
    user = owner(db)
    gateway = StubGateway()
    service = CaptureService(db, gateway)
    capture = service.save(
        user, text="I will analyse today's furnace data more deeply.", request_id=str(uuid4())
    )
    original_date = capture.data_date
    db.commit()
    capture = asyncio.run(service.classify(user, capture.id, external_ai_consent=True))
    task = db.scalar(select(PersonalTask).where(PersonalTask.capture_id == capture.id))
    assert capture.status == "classified"
    assert task.status == "proposed"
    assert task.schedule_basis == "default_tomorrow_requires_review"
    assert db.scalar(select(func.count()).select_from(Reminder)) == 0
    assert capture.data_date == original_date
    asyncio.run(service.classify(user, capture.id, external_ai_consent=True))
    assert gateway.calls == 1


def test_provider_failure_does_not_lose_capture(db):
    user = owner(db)
    service = CaptureService(db, StubGateway(fail=True))
    capture = service.save(
        user, text="Bearing temperature needs checking.", request_id=str(uuid4())
    )
    capture = asyncio.run(service.classify(user, capture.id, external_ai_consent=True))
    assert capture.status == "classification_failed"
    assert db.get(Note, capture.note_id).body == capture.text
    assert "unavailable" in capture.classification_error


def test_model_cannot_choose_other_users_project_or_invent_date(db):
    user, outsider = owner(db), owner(db, "outsider")
    project = Project(owner_id=outsider.id, name="Private")
    db.add(project)
    db.flush()
    output = classification()
    output["project_id"] = project.id
    service = CaptureService(db, StubGateway(output))
    capture = service.save(user, text="Investigate readings", request_id=str(uuid4()))
    capture = asyncio.run(service.classify(user, capture.id, external_ai_consent=True))
    assert capture.status == "classification_failed"
    assert db.get(Note, capture.note_id).project_id is None
    assert db.scalar(select(func.count()).select_from(PersonalTask)) == 0
    output["project_id"], output["requested_date_text"] = None, "tomorrow morning"
    output["suggested_due_at"] = (utcnow() + timedelta(days=1)).isoformat()
    capture = asyncio.run(service.classify(user, capture.id, external_ai_consent=True))
    assert capture.status == "classification_failed"


def test_automatic_reminder_setting_still_supports_conflict_aware_undo(db):
    user = owner(db)
    PersonalRoutineService(db).update_settings(user, {"reminder_mode": "automatic"})
    service = CaptureService(db, StubGateway())
    capture = service.save(
        user, text="I will inspect the furnace readings", request_id=str(uuid4())
    )
    asyncio.run(service.classify(user, capture.id, external_ai_consent=True))
    reminder = db.scalar(select(Reminder))
    action = db.scalar(select(PersonalAction).where(PersonalAction.kind == "create_reminder"))
    assert reminder.status == "open"
    # The toast deadline does not remove internal Undo from Activity history.
    ActionService(db).undo(user, action.id, now=action.undo_until + timedelta(days=1))
    assert reminder.status == "cancelled"
    assert db.scalar(select(PersonalTask)).status == "proposed"
    with pytest.raises(ServiceError, match="undone reminder"):
        ActionService(db).create_reminder(
            user,
            note_id=reminder.note_id,
            title=reminder.title,
            due_at=reminder.due_at,
            request_id=action.request_id,
        )


def test_private_actions_cannot_be_undone_by_another_user(db):
    user, outsider = owner(db), owner(db, "outsider")
    note, action = ActionService(db).create_note(
        user, body="Private work", title="Private", request_id=str(uuid4())
    )
    with pytest.raises(ServiceError, match="not found"):
        ActionService(db).undo(outsider, action.id)
    assert db.get(Note, note.id).body == "Private work"


def test_organization_is_reversible_without_rewriting_notes(db):
    user = owner(db)
    capture = CaptureService(db).save(
        user, text="Keep the exact observation.", request_id=str(uuid4())
    )
    note = db.get(Note, capture.note_id)
    original_text = note.body
    service = OrganizationService(db)
    change = service.preview(user)
    service.apply(user, change.id)
    first_folder_id = db.get(NotePlacement, note.id).folder_id
    service.apply(user, change.id)
    assert db.scalar(select(func.count()).select_from(ProjectFolder)) == 1
    assert note.body == original_text
    service.undo(user, change.id)
    assert note.project_id is None and db.get(NotePlacement, note.id) is None
    second = service.preview(user)
    service.apply(user, second.id)
    assert db.get(NotePlacement, note.id).folder_id == first_folder_id
    assert note.body == original_text


def test_stale_organization_preview_does_not_move_edited_notes(db):
    user = owner(db)
    capture = CaptureService(db).save(user, text="Observation", request_id=str(uuid4()))
    service = OrganizationService(db)
    change = service.preview(user)
    note = db.get(Note, capture.note_id)
    note.body = "A later corrected observation"
    db.flush()
    with pytest.raises(ServiceError, match="changed"):
        service.apply(user, change.id)
    assert db.get(NotePlacement, note.id) is None


def test_daily_checkin_tick_deduplicates_and_stores_original_response(db):
    user = owner(db)
    routine = PersonalRoutineService(db)
    settings = routine.update_settings(
        user, {"daily_checkin_enabled": True, "organization_mode": "off"}
    )
    now = utcnow()
    settings.next_checkin_at = now - timedelta(seconds=1)
    db.flush()
    assert routine.tick(now) == 1
    assert routine.tick(now) == 0
    checkin = routine.start_checkin(user, now)
    assert db.scalar(select(func.count()).select_from(Notification)) == 1
    request_id = str(uuid4())
    routine.complete_checkin(
        user,
        checkin.id,
        text="Today we replaced the bearing. Tomorrow inspect vibration.",
        request_id=request_id,
    )
    routine.complete_checkin(
        user, checkin.id, text="An accidental second submit", request_id=str(uuid4())
    )
    capture = db.get(PersonalCapture, checkin.capture_id)
    assert capture.text == "Today we replaced the bearing. Tomorrow inspect vibration."
    assert checkin.status == "completed"


def test_personal_http_capture_and_settings_roundtrip(client, db):
    from app.auth import hash_password

    user = owner(db)
    user.password_hash = hash_password("Personal-demo-password-83!")
    db.commit()
    login = client.post(
        "/api/auth/login", json={"email": user.email, "password": "Personal-demo-password-83!"}
    )
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    settings = client.patch(
        "/api/personal/settings", json={"timezone": "Europe/London", "reminder_mode": "automatic"}
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()["timezone"] == "Europe/London"
    assert client.get("/api/preferences").json()["timezone"] == "Europe/London"
    result = client.post(
        "/api/personal/captures",
        json={"text": "Today we inspected pump 4.", "request_id": str(uuid4()), "classify": False},
    )
    assert result.status_code == 201, result.text
    payload = result.json()
    assert payload["capture"]["timezone"] == "Europe/London"
    assert payload["note"]["body"] == "Today we inspected pump 4."
    assert len(client.get("/api/personal/captures").json()) == 1
    undo = client.post(f"/api/personal/actions/{payload['actions'][0]['id']}/undo")
    assert undo.status_code == 200, undo.text
    assert client.get("/api/personal/captures").json()[0]["capture"]["status"] == "undone"
