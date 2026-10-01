"""Explicit elapsed and local-wall-clock recurrences without DST drift.

Ambiguous local times run once at the earlier occurrence. Nonexistent times
shift forward by the timezone gap. Supply the immutable initial ``anchor`` when
advancing: it restores the intended local time after a shifted gap. Returned
instants are aware UTC datetimes; machine-local timezone is never consulted.
"""

import math
from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.services.errors import ServiceError

UTC = datetime_timezone.utc


def _instant(value, name):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ServiceError(f"{name} must be an aware ISO timestamp.", 422) from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ServiceError(f"{name} must include a timezone.", 422)
    return value.astimezone(UTC)


def _resolve_local(naive, zone):
    """Round-trip validation: zoneinfo by itself accepts invalid local times."""
    candidates, shifted = [], []
    for fold in (0, 1):
        instant = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        roundtrip = instant.astimezone(zone).replace(tzinfo=None)
        if roundtrip == naive:
            candidates.append(instant)
        elif roundtrip > naive:
            shifted.append((roundtrip, instant))
    if candidates:
        return min(candidates)
    if shifted:
        return min(shifted)[1]
    raise ServiceError("This local schedule time could not be resolved.", 422)


def _occurrence(scheduled_for, interval_minutes, timezone, mode, anchor, direction):
    current = _instant(scheduled_for, "Schedule time")
    if (
        isinstance(interval_minutes, bool)
        or not isinstance(interval_minutes, int)
        or interval_minutes <= 0
    ):
        raise ServiceError("A recurring schedule requires a positive integer interval.", 422)
    if mode not in {"elapsed", "wall_clock"}:
        raise ServiceError("Schedule mode must be elapsed or wall_clock.", 422)
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ServiceError("Select a valid IANA schedule timezone.", 422) from exc
    delta = timedelta(minutes=interval_minutes)
    if mode == "elapsed":
        return current + direction * delta
    origin = _instant(anchor, "Schedule anchor") if anchor is not None else current
    origin_local = origin.astimezone(zone).replace(tzinfo=None)
    current_local = current.astimezone(zone).replace(tzinfo=None)
    periods = (current_local - origin_local).total_seconds() / delta.total_seconds()
    index = math.floor(periods) + 1 if direction > 0 else math.ceil(periods) - 1
    # A skipped day can coalesce intervals; return the next distinct instant.
    for _ in range(10000):
        try:
            candidate = _resolve_local(origin_local + index * delta, zone)
        except OverflowError as exc:
            raise ServiceError("Schedule exceeds the supported date range.", 422) from exc
        if direction * (candidate - current).total_seconds() > 0:
            return candidate
        index += direction
    raise ServiceError("The recurrence could not produce a distinct occurrence.", 422)


def next_occurrence(scheduled_for, interval_minutes, timezone, mode="elapsed", anchor=None):
    """Advance from the scheduled instant, never from delayed worker time."""
    return _occurrence(scheduled_for, interval_minutes, timezone, mode, anchor, 1)


def previous_occurrence(scheduled_for, interval_minutes, timezone, mode="elapsed", anchor=None):
    """Previous local reporting boundary, including seven/nine-hour DST shifts."""
    return _occurrence(scheduled_for, interval_minutes, timezone, mode, anchor, -1)
