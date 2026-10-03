"""Outbound calendar polling. A complete bounded window replaces stale cache entries."""

import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from app.models import User, utcnow
from app.models_connections import CalendarEvent, Connection
from app.connectors.google import GoogleAdapter, google_datetime, google_event_values
from app.connectors.microsoft import MicrosoftAdapter, graph_datetime
from app.repositories.access import AccessRepository
from app.services.connections import ConnectionService
from app.services.errors import ServiceError, ProviderError

CALENDAR_PROVIDERS = ("microsoft", "google")


def microsoft_event_values(event):
    return dict(
        title=(event.get("subject") or "Untitled meeting")[:500],
        cancelled=bool(event.get("isCancelled")),
        busy=event.get("showAs") not in {"free", "workingElsewhere"},
        details={
            "organizer": event.get("organizer"),
            "attendees": event.get("attendees", []),
            "online_meeting": event.get("onlineMeeting"),
            "web_link": event.get("webLink"),
            "provider_modified_at": event.get("lastModifiedDateTime"),
        },
    )


class CalendarSyncService:
    def __init__(self, db, settings=None, http=None):
        self.db = db
        self.connections = ConnectionService(db, settings, http)
        self.settings, self.http = self.connections.settings, http

    async def sync(self, user, rid):
        connection = self.connections.repo.get(user, rid, manage=True, lock=True)
        if connection.provider not in CALENDAR_PROVIDERS:
            raise ServiceError("Only calendar connections can be synchronized.", 422)
        try:
            async with asyncio.timeout(120):
                await self._sync(connection)
        except TimeoutError as exc:
            raise ProviderError(
                "Calendar sync timed out. Select fewer calendars or retry.", retryable=True
            ) from exc
        self.db.flush()
        return {
            "events": self.db.query(CalendarEvent)
            .filter(CalendarEvent.connection_id == connection.id)
            .count(),
            "last_synced_at": connection.last_synced_at,
            "next_sync_at": connection.next_sync_at,
        }

    async def _sync(self, connection):
        start, end = utcnow() - timedelta(days=7), utcnow() + timedelta(days=30)
        selected = connection.config.get("calendar_ids", [])
        if not selected:
            raise ServiceError("Select a calendar before syncing.", 422)
        if connection.provider == "google":
            token = await self.connections.google_token(connection)
            adapter, parse_time, values_of = (
                GoogleAdapter(self.settings, self.http),
                google_datetime,
                google_event_values,
            )
        else:
            token = await self.connections.microsoft_token(connection)
            adapter, parse_time, values_of = (
                MicrosoftAdapter(self.settings, self.http),
                graph_datetime,
                microsoft_event_values,
            )
        incoming = []
        for calendar_id in selected:
            for event in await adapter.events(token, calendar_id, start, end):
                begin, finish = parse_time(event["start"]), parse_time(event["end"])
                if finish < begin:
                    raise ProviderError("Calendar event ends before it begins.")
                incoming.append((calendar_id, event, begin, finish))
                if len(incoming) > 5000:
                    raise ProviderError(
                        "Calendar sync exceeds 5,000 events; select fewer calendars."
                    )
        # Do not delete anything until ALL selected calendar pages have completed.
        existing = {
            (x.calendar_id, x.provider_id): x
            for x in self.db.scalars(
                select(CalendarEvent).where(CalendarEvent.connection_id == connection.id)
            )
        }
        seen = set()
        for calendar_id, event, begin, finish in incoming:
            key = (calendar_id, event["id"])
            seen.add(key)
            row = existing.get(key)
            if row is None:
                row = CalendarEvent(
                    connection_id=connection.id,
                    owner_id=connection.owner_id,
                    calendar_id=calendar_id,
                    provider_id=event["id"],
                )
                self.db.add(row)
            values = dict(starts_at=begin, ends_at=finish, **values_of(event))
            for key, value in values.items():
                if getattr(row, key, None) != value:
                    setattr(row, key, value)
        for key, row in existing.items():
            if key not in seen:
                self.db.delete(row)
        connection.status = "connected"
        connection.last_error = None
        connection.last_synced_at = utcnow()
        connection.next_sync_at = utcnow() + timedelta(minutes=5)

    async def tick(self):
        """Claim one connection; row lock serializes refresh/sync. Owns its commit."""
        row = self.db.scalar(
            select(Connection)
            .where(
                Connection.provider.in_(CALENDAR_PROVIDERS),
                Connection.status.in_(["connected", "error"]),
                Connection.next_sync_at <= utcnow(),
            )
            .order_by(Connection.next_sync_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            self.db.rollback()
            return False
        user = self.db.get(User, row.owner_id)
        if not user or not user.active:
            row.next_sync_at = None
            row.status = "error"
            row.last_error = "Connection owner is inactive."
            self.db.commit()
            return True
        try:
            async with asyncio.timeout(120):
                await self._sync(row)
        except TimeoutError:
            row.status = "error"
            row.last_error = "Calendar sync timed out. Select fewer calendars or retry."
            row.next_sync_at = utcnow() + timedelta(minutes=15)
        except ServiceError as exc:
            row.status = "error"
            row.last_error = exc.detail[:500]
            row.next_sync_at = utcnow() + timedelta(minutes=15)
        self.db.commit()
        return True

    def events(self, user, start, end):
        AccessRepository(self.db).require_active(user)
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start >= end
            or end - start > timedelta(days=60)
        ):
            raise ServiceError("Choose an aware calendar window of at most 60 days.", 422)
        rows = self.db.scalars(
            select(CalendarEvent)
            .join(Connection, Connection.id == CalendarEvent.connection_id)
            .where(
                CalendarEvent.owner_id == user.id,
                Connection.owner_id == user.id,
                Connection.status != "disconnected",
                CalendarEvent.starts_at < end,
                CalendarEvent.ends_at > start,
            )
            .order_by(CalendarEvent.starts_at)
            .limit(1000)
        )
        return [
            {
                key: getattr(row, key)
                for key in (
                    "id",
                    "connection_id",
                    "title",
                    "starts_at",
                    "ends_at",
                    "cancelled",
                    "busy",
                    "details",
                    "updated_at",
                )
            }
            for row in rows
        ]

    def free_slots(self, user, start, end, duration_minutes=30):
        connections = list(
            self.db.scalars(
                select(Connection).where(
                    Connection.owner_id == user.id,
                    Connection.provider.in_(CALENDAR_PROVIDERS),
                    Connection.status.in_(["connected", "error"]),
                )
            )
        )
        fresh = [
            c
            for c in connections
            if c.status == "connected"
            and c.config.get("calendar_ids")
            and c.last_synced_at
            and c.last_synced_at >= utcnow() - timedelta(minutes=15)
        ]
        if not fresh or len(fresh) != len(connections) or not 5 <= duration_minutes <= 480:
            return {"source": "unavailable", "slots": []}
        oldest = min(c.last_synced_at for c in fresh)
        if start < oldest - timedelta(days=7) or end > oldest + timedelta(days=30):
            return {"source": "unavailable", "slots": []}
        busy = sorted(
            [
                (max(e["starts_at"], start), min(e["ends_at"], end))
                for e in self.events(user, start, end)
                if e["busy"] and not e["cancelled"]
            ]
        )
        cursor = start
        slots = []
        duration = timedelta(minutes=duration_minutes)
        for begin, finish in busy + [(end, end)]:
            while cursor + duration <= begin and len(slots) < 20:
                slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
                cursor += duration
            cursor = max(cursor, finish)
        return {"source": "synced_calendar", "as_of": oldest.isoformat(), "slots": slots}
