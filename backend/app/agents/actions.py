"""Explicit personal-chat actions with a durable authorization and undo journal."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.models import utcnow
from app.models_agent import AgentRun
from app.models_personal import AssistantSettings
from app.services.actions import ActionService
from app.services.preferences import PreferenceService
from app.services.errors import ServiceError
from .checkpoints import LeaseLost


@dataclass(frozen=True)
class ActionContext:
    run_id: str
    lease_token: str
    user_query: str
    requested_at: datetime


def explicit_intent(query, action):
    text = query.strip().casefold()
    text = re.sub(r"^(?:hey\s+optimus[,!]?\s*|optimus[,!]?\s*)", "", text)
    prefix = r"^(?:(?:please\s+)|(?:(?:can|could|would)\s+you\s+(?:please\s+)?))?"
    verbs = (
        r"(?:save|write|create|make|take|jot)\b.*\bnote\b|note\s+(?:that|this)\b"
        if action == "note"
        else r"(?:remind\s+me\b|(?:set|create|add)\b.*\breminder\b)"
    )
    return bool(re.search(prefix + "(?:" + verbs + ")", text, re.S))


def deadline_matches(query, excerpt, due_at, requested_at, timezone_name):
    """Accept an explicit date plus clock time; never infer tomorrow from intention.

    ISO dates, today/tomorrow and weekday plus an explicit clock are supported.
    Ambiguous language produces a clarification instead of a silent appointment.
    """
    if not excerpt or excerpt.casefold() not in query.casefold():
        return False
    text = excerpt.casefold()
    zone = ZoneInfo(timezone_name)
    reference = requested_at.astimezone(zone)
    actual = due_at.astimezone(zone)
    iso = re.search(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2})?(?:z|[+-]\d{2}:\d{2})", text)
    if iso:
        try:
            return datetime.fromisoformat(iso[0].replace("z", "+00:00")) == due_at
        except ValueError:
            return False
    date_match = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text)
    if date_match:
        try:
            date = datetime.fromisoformat(date_match[1]).date()
        except ValueError:
            return False
        clock_text = text[date_match.end() :]
    elif re.search(r"\btomorrow\b", text):
        date = (reference + timedelta(days=1)).date()
        clock_text = text
    elif re.search(r"\btoday\b", text):
        date = reference.date()
        clock_text = text
    else:
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        day = next(
            (index for index, name in enumerate(days) if re.search(r"\b" + name + r"\b", text)),
            None,
        )
        if day is None:
            return False
        offset = (day - reference.weekday()) % 7 or 7
        date = (reference + timedelta(days=offset)).date()
        clock_text = text
    clock = re.search(
        r"(?<!\d)(\d{1,2})(?::([0-5]\d))\s*(am|pm)?\b|(?<!\d)(\d{1,2})\s*(am|pm)\b", clock_text
    )
    if not clock:
        return False
    hour = int(clock[1] or clock[4])
    minute = int(clock[2] or 0)
    period = clock[3] or clock[5]
    if period:
        if not 1 <= hour <= 12:
            return False
        hour = hour % 12 + (12 if period == "pm" else 0)
    if hour > 23:
        return False
    # An explicit offset/timezone conflicting with preference is not guessed.
    if (
        actual.date() != date
        or actual.hour != hour
        or actual.minute != minute
        or actual.second != 0
    ):
        return False
    local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
    # DST gaps and folds need clarification instead of silently choosing an offset.
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        return False
    return True


class PersonalActionTools:
    def __init__(self, session, context: ActionContext):
        self.session, self.context = session, context

    def execute(self, user, name, params, call_id, project_id=None):
        context = self.context
        run = self.session.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == context.run_id,
                AgentRun.owner_id == user.id,
                AgentRun.workspace_id.is_(None),
                AgentRun.status == "running",
                AgentRun.lease_token == context.lease_token,
                AgentRun.lease_until > utcnow(),
            )
            .with_for_update()
        )
        if run is None:
            raise LeaseLost()
        if not call_id or len(call_id) > 200:
            raise ServiceError("A durable tool invocation is required.", 422)
        action = "note" if name == "save_personal_note" else "reminder"
        if not explicit_intent(context.user_query, action):
            return (
                {
                    "error": "This change was not explicitly requested. Ask the user before creating it."
                },
                [],
                [],
            )
        key = "agent:" + hashlib.sha256((context.run_id + ":" + call_id).encode()).hexdigest()
        service = ActionService(self.session)
        if action == "reminder":
            settings = self.session.get(AssistantSettings, user.id)
            if settings and settings.reminder_mode == "off":
                return (
                    {"error": "Reminder automation is disabled in your assistant settings."},
                    [],
                    [],
                )
            timezone_name = PreferenceService(self.session).get(user)["timezone"]
            if not deadline_matches(
                context.user_query,
                params.user_deadline_text,
                params.due_at,
                context.requested_at,
                timezone_name,
            ):
                return (
                    {
                        "error": "The requested date and clock time are not unambiguous. Ask for an explicit date/time before creating the reminder."
                    },
                    [],
                    [],
                )
        note, note_action = service.create_note(
            user,
            body=params.body,
            title=params.title,
            request_id=key + ":note",
            project_id=project_id,
        )
        refs = [
            {
                "type": "note",
                "id": note.id,
                "title": note.title,
                "version": note.updated_at.isoformat(),
            }
        ]
        actions = [
            {
                "id": note_action.id,
                "kind": note_action.kind,
                "undo_until": note_action.undo_until.isoformat(),
            }
        ]
        output = {"created": True, "note_id": note.id}
        if action == "reminder":
            reminder, reminder_action = service.create_reminder(
                user,
                note_id=note.id,
                title=params.title,
                due_at=params.due_at,
                request_id=key + ":reminder",
            )
            refs.append(
                {
                    "type": "reminder",
                    "id": reminder.id,
                    "title": reminder.title,
                    "version": reminder.updated_at.isoformat(),
                }
            )
            actions.insert(
                0,
                {
                    "id": reminder_action.id,
                    "kind": reminder_action.kind,
                    "undo_until": reminder_action.undo_until.isoformat(),
                },
            )
            output.update(reminder_id=reminder.id, due_at=reminder.due_at.isoformat())
        output["actions"] = actions
        existing = list((run.context_metadata or {}).get("actions", []))
        run.context_metadata = {
            **(run.context_metadata or {}),
            "actions": list({a["id"]: a for a in [*existing, *actions]}.values()),
        }
        # All writes, idempotency keys and undo records commit under the run fence.
        self.session.commit()
        return output, refs, actions
