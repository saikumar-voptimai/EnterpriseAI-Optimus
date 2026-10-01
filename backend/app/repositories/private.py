"""Owner-scoped personal context persistence. These methods flush, never commit."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Memory, Note, Project, Reminder, User, utcnow
from .access import AccessRepository


class PrivateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.access = AccessRepository(session)

    def projects(self, user: User, *, include_archived: bool = True) -> Sequence[Project]:
        self.access.require_active(user)
        stmt = select(Project).where(Project.owner_id == user.id)
        if not include_archived:
            stmt = stmt.where(Project.archived.is_(False))
        return self.session.scalars(stmt.order_by(Project.updated_at.desc(), Project.id)).all()

    def create_project(self, user: User, *, name: str, goal: str = "") -> Project:
        self.access.require_active(user)
        project = Project(owner_id=user.id, name=name, goal=goal)
        self.session.add(project)
        self.session.flush()
        return project

    def notes(
        self,
        user: User,
        *,
        project_id: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> Sequence[Note]:
        self.access.require_active(user)
        stmt = select(Note).where(Note.owner_id == user.id)
        if project_id is not None:
            self.access.project(user, project_id)
            stmt = stmt.where(Note.project_id == project_id)
        if query:
            # SQLAlchemy escapes LIKE wildcards so a literal search remains literal.
            stmt = stmt.where(
                Note.title.icontains(query, autoescape=True)
                | Note.body.icontains(query, autoescape=True)
            )
        return self.session.scalars(
            stmt.order_by(Note.updated_at.desc(), Note.id).limit(limit)
        ).all()

    def create_note(
        self,
        user: User,
        *,
        body: str,
        title: str = "",
        kind: str = "thought",
        project_id: str | None = None,
    ) -> Note:
        self.access.require_active(user)
        if project_id is not None:
            self.access.project(user, project_id)
        note = Note(owner_id=user.id, project_id=project_id, title=title, body=body, kind=kind)
        self.session.add(note)
        self.session.flush()
        return note

    def memories(self, user: User) -> Sequence[Memory]:
        self.access.require_active(user)
        return self.session.scalars(
            select(Memory)
            .where(Memory.owner_id == user.id, Memory.forgotten_at.is_(None))
            .order_by(Memory.updated_at.desc(), Memory.id)
        ).all()

    def create_memory(self, user: User, *, note_id: str, text: str) -> Memory:
        self.access.note(user, note_id)
        memory = Memory(owner_id=user.id, note_id=note_id, text=text)
        self.session.add(memory)
        self.session.flush()
        return memory

    def forget_memory(self, user: User, memory_id: str) -> Memory:
        memory = self.access.memory(user, memory_id)
        memory.forgotten_at = utcnow()
        self.session.flush()
        return memory

    def reminders(self, user: User) -> Sequence[Reminder]:
        self.access.require_active(user)
        return self.session.scalars(
            select(Reminder)
            .where(Reminder.owner_id == user.id)
            .order_by(Reminder.due_at, Reminder.id)
        ).all()

    def create_reminder(
        self, user: User, *, note_id: str, title: str, due_at: datetime
    ) -> Reminder:
        self.access.note(user, note_id)
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must include a timezone")
        reminder = Reminder(owner_id=user.id, note_id=note_id, title=title, due_at=due_at)
        self.session.add(reminder)
        self.session.flush()
        return reminder
