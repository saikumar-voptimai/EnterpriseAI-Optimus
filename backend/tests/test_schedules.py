"""Scheduler cadence, DST folds/gaps, and adjacent reporting boundaries."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from app.services.errors import ServiceError
from app.services.schedules import next_occurrence, previous_occurrence

UTC = timezone.utc


def local(year, month, day, hour, minute=0, name="Europe/London", fold=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(name), fold=fold)


def test_elapsed_validation_cadence_is_fifteen_minutes_across_dst():
    anchor = local(2026, 3, 29, 0, 55)
    result = next_occurrence(anchor, 15, "Europe/London")
    assert result - anchor.astimezone(UTC) == timedelta(minutes=15)
    assert result.astimezone(ZoneInfo("Europe/London")).hour == 2


@pytest.mark.parametrize("start,hours", [(local(2026, 3, 28, 8), 23), (local(2026, 10, 24, 8), 25)])
def test_daily_wall_clock_preserves_eight_am(start, hours):
    result = next_occurrence(start, 1440, "Europe/London", "wall_clock", anchor=start)
    assert result.astimezone(ZoneInfo("Europe/London")).hour == 8
    assert (result - start.astimezone(UTC)).total_seconds() / 3600 == hours


def test_spring_gap_shifts_once_then_returns_to_original_anchor():
    anchor = local(2026, 3, 28, 1, 30)
    shifted = next_occurrence(anchor, 1440, "Europe/London", "wall_clock", anchor=anchor)
    assert (
        shifted.astimezone(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M")
        == "2026-03-29 02:30"
    )
    following = next_occurrence(shifted, 1440, "Europe/London", "wall_clock", anchor=anchor)
    assert (
        following.astimezone(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M")
        == "2026-03-30 01:30"
    )


def test_fall_fold_runs_once_at_first_occurrence():
    anchor = local(2026, 10, 24, 1, 30)
    repeated = next_occurrence(anchor, 1440, "Europe/London", "wall_clock", anchor=anchor)
    assert repeated == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    following = next_occurrence(repeated, 1440, "Europe/London", "wall_clock", anchor=anchor)
    assert following == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)


@pytest.mark.parametrize("anchor,hours", [(local(2026, 3, 29, 0), 7), (local(2026, 10, 25, 0), 9)])
def test_shift_boundaries_remain_adjacent_across_dst(anchor, hours):
    result = next_occurrence(anchor, 480, "Europe/London", "wall_clock", anchor=anchor)
    assert result.astimezone(ZoneInfo("Europe/London")).hour == 8
    assert (result - anchor.astimezone(UTC)).total_seconds() / 3600 == hours
    assert previous_occurrence(
        result, 480, "Europe/London", "wall_clock", anchor=anchor
    ) == anchor.astimezone(UTC)


def test_weekly_local_schedule_and_non_dst_offset():
    anchor = local(2026, 3, 23, 8)
    result = next_occurrence(
        anchor, 10080, "Europe/London", "wall_clock", anchor=anchor.isoformat()
    )
    assert result.astimezone(ZoneInfo("Europe/London")).strftime("%A %H:%M") == "Monday 08:00"
    kolkata = local(2026, 3, 29, 6, name="Asia/Kolkata")
    assert next_occurrence(kolkata, 480, "Asia/Kolkata", "wall_clock") == kolkata.astimezone(
        UTC
    ) + timedelta(hours=8)


def test_non_hour_dst_gap_and_skipped_day():
    anchor = local(2026, 10, 3, 2, 15, name="Australia/Lord_Howe")
    shifted = next_occurrence(anchor, 1440, "Australia/Lord_Howe", "wall_clock", anchor=anchor)
    assert shifted.astimezone(ZoneInfo("Australia/Lord_Howe")).strftime("%H:%M") == "02:45"
    follow = next_occurrence(shifted, 1440, "Australia/Lord_Howe", "wall_clock", anchor=anchor)
    assert follow.astimezone(ZoneInfo("Australia/Lord_Howe")).strftime("%H:%M") == "02:15"
    samoa = local(2011, 12, 29, 8, name="Pacific/Apia")
    shifted = next_occurrence(samoa, 1440, "Pacific/Apia", "wall_clock", anchor=samoa)
    follow = next_occurrence(shifted, 1440, "Pacific/Apia", "wall_clock", anchor=samoa)
    assert shifted.astimezone(ZoneInfo("Pacific/Apia")).day == 31
    assert (
        follow.astimezone(ZoneInfo("Pacific/Apia")).strftime("%Y-%m-%d %H:%M") == "2012-01-01 08:00"
    )


@pytest.mark.parametrize(
    "interval,zone,mode",
    [
        (0, "UTC", "elapsed"),
        (True, "UTC", "elapsed"),
        (1.5, "UTC", "elapsed"),
        (15, "Invalid/Zone", "elapsed"),
        (15, "UTC", "invalid"),
    ],
)
def test_invalid_schedule_is_explicit(interval, zone, mode):
    with pytest.raises(ServiceError):
        next_occurrence(datetime(2026, 1, 1, tzinfo=UTC), interval, zone, mode)
    with pytest.raises(ServiceError):
        next_occurrence(datetime(2026, 1, 1), 15, "UTC")
