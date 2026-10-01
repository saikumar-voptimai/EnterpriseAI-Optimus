"""Dependencies join exact required schedule instances, not arbitrary last successes."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from app.services.jobs import compatible_dependency, dependency_occurrence
from app.services.schedules import next_occurrence

UTC = timezone.utc


def job(anchor, minutes, mode="elapsed", zone="UTC", owner="employee", workspace="unit"):
    return SimpleNamespace(
        owner_id=owner,
        workspace_id=workspace,
        interval_minutes=minutes,
        next_run_at=anchor,
        timezone=zone,
        config={"schedule_anchor": anchor.isoformat(), "schedule_mode": mode},
    )


def test_fifteen_minute_validation_joins_eight_hour_shift_with_different_phase():
    validation = job(datetime(2026, 9, 1, 0, 2, tzinfo=UTC), 15)
    handover = job(datetime(2026, 9, 1, 8, 0, tzinfo=UTC), 480)
    assert compatible_dependency(handover, validation)
    expected = dependency_occurrence(handover, validation, datetime(2026, 9, 1, 16, 0, tzinfo=UTC))
    assert expected == datetime(2026, 9, 1, 15, 47, tzinfo=UTC)
    assert datetime(2026, 9, 1, 16, 0, tzinfo=UTC) - expected < timedelta(minutes=15)


def test_exact_aligned_occurrence_is_not_shifted_to_previous_validation():
    anchor = datetime(2026, 9, 1, tzinfo=UTC)
    validation, handover = job(anchor, 15), job(anchor, 480)
    current = anchor + timedelta(hours=8)
    assert dependency_occurrence(handover, validation, current) == current
    validation.next_run_at = current + timedelta(days=10)
    assert dependency_occurrence(handover, validation, current) == current


def test_recurring_dependency_has_not_started_is_unavailable():
    anchor = datetime(2026, 9, 1, tzinfo=UTC)
    parent, dependency = job(anchor, 480), job(anchor + timedelta(hours=1), 15)
    assert dependency_occurrence(parent, dependency, anchor) is None


@pytest.mark.parametrize(
    "owner,workspace,period",
    [
        ("other", "unit", 15),
        ("employee", "other", 15),
        ("employee", "unit", 720),
        ("employee", "unit", 17),
    ],
)
def test_incompatible_audience_or_period_is_rejected(owner, workspace, period):
    anchor = datetime(2026, 9, 1, tzinfo=UTC)
    assert not compatible_dependency(
        job(anchor, 480), job(anchor, period, owner=owner, workspace=workspace)
    )


def test_one_shots_retain_their_single_aligned_occurrence():
    anchor = datetime(2026, 9, 1, tzinfo=UTC)
    parent, dependency = job(anchor, None), job(anchor, None)
    assert compatible_dependency(parent, dependency)
    assert dependency_occurrence(parent, dependency, anchor) == anchor
    assert not compatible_dependency(parent, job(anchor + timedelta(minutes=1), None))


@pytest.mark.parametrize("month,day", [(3, 29), (10, 25)])
def test_wall_clock_shift_and_elapsed_validator_join_across_dst(month, day):
    zone = ZoneInfo("Europe/London")
    anchor = datetime(2026, month, day - 1, 0, 0, tzinfo=zone)
    parent = job(anchor, 480, "wall_clock", "Europe/London")
    dependency = job(anchor + timedelta(minutes=2), 15)
    stop = datetime(2026, month, day, 8, 0, tzinfo=zone).astimezone(UTC)
    result = dependency_occurrence(parent, dependency, stop)
    assert timedelta(0) <= stop - result < timedelta(minutes=15)
    assert (result - anchor.astimezone(UTC) - timedelta(minutes=2)) % timedelta(
        minutes=15
    ) == timedelta(0)


def test_wall_clock_dependency_gap_is_an_exact_resolved_occurrence():
    zone = ZoneInfo("Europe/London")
    anchor = datetime(2026, 3, 28, 1, 30, tzinfo=zone)
    dependency = job(anchor, 1440, "wall_clock", "Europe/London")
    parent = job(anchor, 10080, "wall_clock", "Europe/London")
    stop = datetime(2026, 3, 29, 3, 0, tzinfo=zone)
    assert dependency_occurrence(parent, dependency, stop) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )


def test_wall_clock_fold_chooses_latest_required_instant_even_when_clock_went_back():
    zone = ZoneInfo("Europe/London")
    anchor = datetime(2026, 10, 24, 0, 5, tzinfo=zone)
    dependency = job(anchor, 15, "wall_clock", "Europe/London")
    parent = job(anchor, 480, "wall_clock", "Europe/London")
    stop = datetime(2026, 10, 25, 1, 10, tzinfo=zone, fold=1)
    expected = dependency_occurrence(parent, dependency, stop)
    assert expected == datetime(2026, 10, 25, 0, 50, tzinfo=UTC)
    assert next_occurrence(
        expected, 15, "Europe/London", "wall_clock", anchor=anchor
    ) > stop.astimezone(UTC)
