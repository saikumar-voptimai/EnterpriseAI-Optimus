"""Authorization and query boundary for provider accounts and meeting rooms."""

from sqlalchemy import or_, select
from app.models import User
from app.models_connections import Connection, MeetingRoom, MeetingAttendee
from app.repositories.access import AccessRepository, NotFound, AuthorizationError


class ConnectionRepository:
    def __init__(self, db):
        self.db, self.access = db, AccessRepository(db)

    def list(self, user, workspace_id=None):
        self.access.require_active(user)
        stmt = select(Connection).where(
            or_(
                (Connection.owner_id == user.id) & (Connection.workspace_id.is_(None)),
                Connection.workspace_id.in_(self.access.workspace_ids(user)),
            )
        )
        if workspace_id is not None:
            self.access.workspace(user, workspace_id)
            stmt = stmt.where(Connection.workspace_id == workspace_id)
        return list(self.db.scalars(stmt.order_by(Connection.created_at.desc()).limit(200)))

    def get(self, user, rid, *, manage=False, lock=False):
        self.access.require_active(user)
        stmt = select(Connection).where(Connection.id == rid)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        obj = self.db.scalar(stmt)
        if obj is None:
            raise NotFound("Connection not found")
        if obj.workspace_id:
            self.access.workspace(user, obj.workspace_id, roles={"manager"} if manage else None)
        elif obj.owner_id != user.id:
            raise NotFound("Connection not found")
        return obj

    def meeting(self, user, rid, *, manage=False, lock=False):
        self.access.require_active(user)
        stmt = select(MeetingRoom).where(MeetingRoom.id == rid)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        room = self.db.scalar(stmt)
        if room is None or (
            room.owner_id != user.id and self.db.get(MeetingAttendee, (rid, user.id)) is None
        ):
            raise NotFound("Meeting room not found")
        if manage and room.owner_id != user.id:
            raise AuthorizationError("Only the meeting owner can review and publish minutes")
        return room
