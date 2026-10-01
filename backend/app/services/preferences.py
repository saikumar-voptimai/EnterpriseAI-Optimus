"""Typed preferences: shared presentation settings never include personal instructions."""

from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.models import User, utcnow
from app.models_knowledge import UserPreference
from app.repositories import AccessRepository
from app.services.errors import ServiceError

DEFAULTS = {
    "language": "English",
    "timezone": "Asia/Kolkata",
    "units": "SI",
    "response_style": "concise",
    "personal_instructions": "",
}
SHAREABLE = ("language", "timezone", "units", "response_style")


class PreferenceService:
    def __init__(self, session):
        self.session = session

    def get(self, user):
        AccessRepository.require_active(user)
        row = self.session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
        if row is None:
            return {**DEFAULTS, "version": "default"}
        return {
            **{key: getattr(row, key) for key in DEFAULTS},
            "id": row.id,
            "version": row.updated_at.astimezone(timezone.utc).isoformat(),
        }

    def update(self, user, changes):
        AccessRepository.require_active(user)
        locked_user = self.session.scalar(select(User).where(User.id == user.id).with_for_update())
        if locked_user is None:
            raise ServiceError("The user is no longer available.", 403)
        AccessRepository.require_active(locked_user)
        if any(key not in DEFAULTS for key in changes):
            raise ServiceError("Unknown preference field.", 422)
        if "timezone" in changes:
            try:
                ZoneInfo(changes["timezone"])
            except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
                raise ServiceError(
                    "Choose a valid IANA timezone such as Asia/Kolkata.", 422
                ) from exc
        self.session.execute(
            insert(UserPreference)
            .values(user_id=user.id, **DEFAULTS)
            .on_conflict_do_nothing(index_elements=[UserPreference.user_id])
        )
        row = self.session.scalar(
            select(UserPreference).where(UserPreference.user_id == user.id).with_for_update()
        )
        for key, value in changes.items():
            setattr(row, key, value)
        if "timezone" in changes:
            # Both settings endpoints use the same timezone and owner lock.
            from app.models_personal import AssistantSettings
            from app.services.personal_routines import next_local_time

            automation = self.session.get(AssistantSettings, user.id)
            if automation and automation.daily_checkin_enabled:
                automation.next_checkin_at = next_local_time(
                    utcnow(), changes["timezone"], automation.daily_checkin_time
                )
        self.session.flush()
        return self.get(user)

    def context(self, user, *, shared=False):
        preferences = self.get(user)
        keys = SHAREABLE if shared else (*SHAREABLE, "personal_instructions")
        return {key: preferences[key] for key in keys}, preferences["version"]
