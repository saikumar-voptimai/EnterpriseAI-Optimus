"""Owner scoped access and serialization for the assistant extension."""

from datetime import timedelta
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import User, utcnow
from app.models_personal import AssistantSettings, PersonalAction, PersonalCapture, ProjectFolder
from app.repositories.access import AccessRepository
from app.services.errors import ServiceError

T = TypeVar("T")


def record(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class PersonalAssistantRepository:
    def __init__(self, db: Session):
        self.db = db
        self.access = AccessRepository(db)

    def settings(self, user: User) -> AssistantSettings:
        self.access.require_active(user)
        self.db.execute(
            insert(AssistantSettings)
            .values(owner_id=user.id, next_organization_at=utcnow() + timedelta(days=7))
            .on_conflict_do_nothing(index_elements=["owner_id"])
        )
        return self.db.get(AssistantSettings, user.id)

    def owned(self, cls: type[T], user: User, rid: str, *, lock: bool = False) -> T:
        self.access.require_active(user)
        query = select(cls).where(cls.id == rid, cls.owner_id == user.id)
        if lock:
            query = query.with_for_update()
        obj = self.db.scalar(query)
        if obj is None:
            raise ServiceError("Personal item not found.", 404)
        return obj

    def action_by_request(self, user: User, request_id: str):
        self.access.require_active(user)
        return self.db.scalar(
            select(PersonalAction).where(
                PersonalAction.owner_id == user.id, PersonalAction.request_id == request_id
            )
        )

    def capture_by_request(self, user: User, request_id: str):
        self.access.require_active(user)
        return self.db.scalar(
            select(PersonalCapture).where(
                PersonalCapture.owner_id == user.id, PersonalCapture.request_id == request_id
            )
        )

    def folder(self, user: User, folder_id: str) -> ProjectFolder:
        return self.owned(ProjectFolder, user, folder_id)

    def lock_owner(self, user: User):
        # Serializes personal mutations and their idempotency keys, not model calls.
        if user is None:
            raise ServiceError("The account is no longer active.", 401)
        locked = self.db.scalar(select(User).where(User.id == user.id).with_for_update())
        if locked is None:
            raise ServiceError("The account is no longer active.", 401)
        self.access.require_active(locked)
        return locked
