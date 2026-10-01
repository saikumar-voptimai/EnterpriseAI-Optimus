"""Small scheduler ticks for local-time check-ins and weekly organization.

No model or network operation runs in tick(). The caller commits the batch.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.models import Notification, User, utcnow
from app.models_personal import AssistantSettings, DailyCheckin
from app.repositories.personal_assistant import PersonalAssistantRepository
from app.services.capture import CaptureService
from app.services.organization import OrganizationService
from app.services.preferences import PreferenceService

CHECKIN_PROMPTS = [
    "What happened today? Mention observations, work completed or decisions.",
    "What remains unresolved, and is anything blocking you?",
    "What should you or your colleagues follow up on next?",
]


def next_local_time(now: datetime, zone_name: str, clock: str) -> datetime:
    """Choose the next local clock time; normalize nonexistent DST wall times."""
    zone = ZoneInfo(zone_name)
    local = now.astimezone(zone)
    hour, minute = (int(part) for part in clock.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    # UTC roundtrip normalizes spring-forward gaps rather than silently skipping a day.
    return candidate.astimezone(timezone.utc)


class PersonalRoutineService:
    def __init__(self, db):
        self.db = db
        self.repo = PersonalAssistantRepository(db)

    def settings_payload(self, user):
        from app.repositories.personal_assistant import record

        return {
            **record(self.repo.settings(user)),
            "timezone": PreferenceService(self.db).get(user)["timezone"],
        }

    def update_settings(self, user, changes):
        self.repo.lock_owner(user)
        settings = self.repo.settings(user)
        values = dict(changes)
        if "timezone" in values:
            PreferenceService(self.db).update(user, {"timezone": values.pop("timezone")})
        for key, value in values.items():
            setattr(settings, key, value)
        zone = PreferenceService(self.db).get(user)["timezone"]
        now = utcnow()
        settings.next_checkin_at = (
            next_local_time(now, zone, settings.daily_checkin_time)
            if settings.daily_checkin_enabled
            else None
        )
        if settings.organization_mode == "off":
            settings.next_organization_at = None
        elif settings.next_organization_at is None:
            settings.next_organization_at = now + timedelta(days=7)
        self.db.flush()
        return settings

    def start_checkin(self, user, now=None):
        self.repo.lock_owner(user)
        now = now or utcnow()
        zone = ZoneInfo(PreferenceService(self.db).get(user)["timezone"])
        local_date = now.astimezone(zone).date()
        checkin = self.db.scalar(
            select(DailyCheckin).where(
                DailyCheckin.owner_id == user.id, DailyCheckin.local_date == local_date
            )
        )
        if checkin:
            return checkin
        checkin = DailyCheckin(
            owner_id=user.id,
            local_date=local_date,
            status="active",
            prompts=CHECKIN_PROMPTS,
            expires_at=now + timedelta(minutes=5),
        )
        self.db.add(checkin)
        self.db.flush()
        return checkin

    def complete_checkin(self, user, checkin_id, *, text, request_id):
        self.repo.lock_owner(user)
        checkin = self.repo.owned(DailyCheckin, user, checkin_id, lock=True)
        if checkin.capture_id:
            return checkin
        capture = CaptureService(self.db).save(user, text=text, request_id=request_id)
        checkin.capture_id, checkin.status = capture.id, "completed"
        self.db.flush()
        return checkin

    def tick(self, now=None, limit=50):
        now = now or utcnow()
        settings_rows = list(
            self.db.scalars(
                select(AssistantSettings)
                .join(User, User.id == AssistantSettings.owner_id)
                .where(
                    or_(
                        AssistantSettings.next_checkin_at <= now,
                        AssistantSettings.next_organization_at <= now,
                    )
                )
                .order_by(AssistantSettings.owner_id)
                .with_for_update(of=User, skip_locked=True)
                .limit(limit)
            )
        )
        processed = 0
        for settings in settings_rows:
            user = self.db.get(User, settings.owner_id)
            if not user or not user.active:
                settings.next_checkin_at, settings.next_organization_at = None, None
                continue
            zone = PreferenceService(self.db).get(user)["timezone"]
            if settings.next_checkin_at and settings.next_checkin_at <= now:
                if settings.daily_checkin_enabled:
                    checkin = self.start_checkin(user, now)
                    key = f"checkin:{checkin.id}"
                    if not self.db.scalar(
                        select(Notification.id).where(Notification.dedupe_key == key)
                    ):
                        self.db.add(
                            Notification(
                                user_id=user.id,
                                kind="daily_checkin",
                                title="Your five-minute check-in",
                                body="What happened today, what is unresolved, and what should happen next? Open Optimus to capture your update.",
                                resource_type="checkin",
                                resource_id=checkin.id,
                                dedupe_key=key,
                            )
                        )
                    settings.next_checkin_at = next_local_time(
                        now, zone, settings.daily_checkin_time
                    )
                else:
                    settings.next_checkin_at = None
            if settings.next_organization_at and settings.next_organization_at <= now:
                if settings.organization_mode != "off":
                    change = OrganizationService(self.db).preview(user)
                    if change.moves:
                        if settings.organization_mode == "automatic":
                            OrganizationService(self.db).apply(user, change.id)
                        self.db.add(
                            Notification(
                                user_id=user.id,
                                kind="organization",
                                title="Weekly notebook organization",
                                body=f"{len(change.moves)} notes {'filed; Undo is available in Activity' if change.status == 'applied' else 'ready to file; review the preview in Optimus'}.",
                                resource_type="organization",
                                resource_id=change.id,
                                dedupe_key=f"organization:{change.id}",
                            )
                        )
                    settings.next_organization_at = now + timedelta(days=7)
                else:
                    settings.next_organization_at = None
            processed += 1
        self.db.flush()
        return processed
