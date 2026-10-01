"""Transactional action journal with idempotent replay and conflict-aware Undo.

Internal changes are visible immediately and reversible after the toast expires.
External email and Teams operations belong to DeliveryService’s durable outbox;
there is no second external delivery queue in this journal. Application services,
not model output, select the supported operation and payload shape.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Memory, Note, Reminder, User, utcnow
from app.models_personal import PersonalAction, PersonalCapture, PersonalTask
from app.repositories.personal_assistant import PersonalAssistantRepository
from app.services.errors import ServiceError


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ServiceError("A timestamp with its timezone is required.", 422)
    return value


class ActionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = PersonalAssistantRepository(db)

    def _record(
        self, user, *, request_id, kind, resource_id=None, payload=None, pending=False, now=None
    ):
        now = now or utcnow()
        action = PersonalAction(
            owner_id=user.id,
            request_id=request_id,
            kind=kind,
            resource_id=resource_id,
            payload=payload or {},
            status="pending" if pending else "applied",
            undo_until=now + timedelta(seconds=20),
        )
        self.db.add(action)
        self.db.flush()
        return action

    def create_note(
        self,
        user: User,
        *,
        body: str,
        title: str,
        request_id: str,
        project_id: str | None = None,
        kind: str = "thought",
    ):
        self.repo.lock_owner(user)
        previous = self.repo.action_by_request(user, request_id)
        if previous:
            if (
                previous.status != "applied"
                or previous.kind != "create_note"
                or previous.payload
                != {"body": body, "title": title[:300], "kind": kind, "project_id": project_id}
            ):
                raise ServiceError(
                    "This request ID was already used for another or undone action.", 409
                )
            return self.db.get(Note, previous.resource_id), previous
        if project_id:
            self.repo.access.project(user, project_id)
        note = Note(
            owner_id=user.id, project_id=project_id, body=body, title=title[:300], kind=kind
        )
        self.db.add(note)
        self.db.flush()
        action = self._record(
            user,
            request_id=request_id,
            kind="create_note",
            resource_id=note.id,
            payload={"body": body, "title": note.title, "kind": kind, "project_id": project_id},
        )
        return note, action

    def create_reminder(
        self, user: User, *, note_id: str, title: str, due_at: datetime, request_id: str
    ):
        self.repo.lock_owner(user)
        previous = self.repo.action_by_request(user, request_id)
        if previous:
            expected = previous.payload
            if (
                previous.status != "applied"
                or previous.kind != "create_reminder"
                or expected.get("note_id") != note_id
                or expected.get("title") != title[:300]
                or datetime.fromisoformat(expected["due_at"]) != due_at
            ):
                raise ServiceError(
                    "This request ID was already used for another or undone reminder.", 409
                )
            return self.db.get(Reminder, previous.resource_id), previous
        self.repo.access.note(user, note_id)
        due_at = aware(due_at)
        if due_at <= utcnow():
            raise ServiceError("Choose a future reminder time.", 422)
        reminder = Reminder(
            owner_id=user.id, note_id=note_id, title=title[:300], due_at=due_at, status="open"
        )
        self.db.add(reminder)
        self.db.flush()
        action = self._record(
            user,
            request_id=request_id,
            kind="create_reminder",
            resource_id=reminder.id,
            payload={"title": reminder.title, "due_at": due_at.isoformat(), "note_id": note_id},
        )
        return reminder, action

    def create_memory(self, user: User, *, note_id: str, text: str, request_id: str):
        self.repo.lock_owner(user)
        previous = self.repo.action_by_request(user, request_id)
        if previous:
            if (
                previous.status != "applied"
                or previous.kind != "create_memory"
                or previous.payload != {"text": text, "note_id": note_id}
            ):
                raise ServiceError(
                    "This request ID was already used for another or undone memory.", 409
                )
            return self.db.get(Memory, previous.resource_id), previous
        self.repo.access.note(user, note_id)
        memory = Memory(owner_id=user.id, note_id=note_id, text=text)
        self.db.add(memory)
        self.db.flush()
        action = self._record(
            user,
            request_id=request_id,
            kind="create_memory",
            resource_id=memory.id,
            payload={"text": text, "note_id": note_id},
        )
        return memory, action

    def undo(self, user: User, action_id: str, *, now: datetime | None = None):
        self.repo.lock_owner(user)
        action = self.repo.owned(PersonalAction, user, action_id, lock=True)
        if action.status in {"undone", "cancelled"}:
            return action
        now = now or utcnow()
        if action.status == "pending":
            # A dispatcher also locks this row, so cancellation cannot race dispatch.
            action.status, action.undone_at = "cancelled", now
            self.db.flush()
            return action
        if action.status != "applied":
            raise ServiceError("This action cannot be undone.", 409)
        if action.kind == "create_reminder":
            obj = self.db.scalar(
                select(Reminder).where(Reminder.id == action.resource_id).with_for_update()
            )
            if obj and (
                obj.owner_id != user.id
                or obj.title != action.payload["title"]
                or obj.due_at != datetime.fromisoformat(action.payload["due_at"])
                or obj.status not in {"open", "cancelled"}
                or obj.notified_at is not None
            ):
                raise ServiceError(
                    "The reminder changed or was delivered; review it before cancelling.", 409
                )
            if obj:
                obj.status = "cancelled"
                for task in self.db.scalars(
                    select(PersonalTask).where(PersonalTask.reminder_id == obj.id)
                ):
                    task.reminder_id, task.status = None, "proposed"
        elif action.kind == "create_memory":
            obj = self.db.scalar(
                select(Memory).where(Memory.id == action.resource_id).with_for_update()
            )
            if obj and (
                obj.owner_id != user.id
                or (obj.forgotten_at is None and obj.text != action.payload["text"])
            ):
                raise ServiceError("The memory was edited; review it before undoing.", 409)
            if obj:
                obj.forgotten_at, obj.text = now, ""
        elif action.kind == "create_note":
            obj = self.db.scalar(
                select(Note).where(Note.id == action.resource_id).with_for_update()
            )
            if obj:
                if obj.owner_id != user.id or any(
                    getattr(obj, key) != value for key, value in action.payload.items()
                ):
                    raise ServiceError(
                        "The note was edited or filed; review it before undoing.", 409
                    )
                if self.db.scalar(
                    select(Reminder.id)
                    .where(Reminder.note_id == obj.id, Reminder.status != "cancelled")
                    .limit(1)
                ) or self.db.scalar(
                    select(Memory.id)
                    .where(Memory.note_id == obj.id, Memory.forgotten_at.is_(None))
                    .limit(1)
                ):
                    raise ServiceError("Undo the linked reminder or memory first.", 409)
                for capture in self.db.scalars(
                    select(PersonalCapture).where(PersonalCapture.note_id == obj.id)
                ):
                    capture.status, capture.note_id = "undone", None
                self.db.flush()
                self.db.delete(obj)
        elif action.kind == "organize_notes":
            from .organization import OrganizationService

            OrganizationService(self.db)._undo_moves(user, action.resource_id)
        else:
            raise ServiceError(
                "This action has already been dispatched and cannot be recalled.", 409
            )
        action.status, action.undone_at = "undone", now
        self.db.flush()
        return action
