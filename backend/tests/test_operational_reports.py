"""Numeric reporting tests exercise actual boundaries, counter semantics and rates."""

from datetime import datetime, timedelta, timezone
import pytest
from app.services.operational_reports import (
    MetricConfig,
    evaluate_series,
    compare_windows,
    reporting_windows,
)

T = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def metric(**kwargs):
    return MetricConfig(
        connection_id="connection",
        bucket_id="bucket",
        measurement="production",
        field="tonnes",
        **kwargs,
    )


def points(values, start=None, interval=60):
    start = start or T - timedelta(minutes=len(values))
    return [
        {"timestamp": (start + timedelta(seconds=i * interval)).isoformat(), "value": v}
        for i, v in enumerate(values)
    ]


def test_validation_checks_missing_stale_limits_without_fabricating_scores():
    config = metric(min=1, max=10, max_age_minutes=2, expected_interval_seconds=60)
    result = evaluate_series(points([4] * 15), config, T - timedelta(minutes=15), T)
    assert result["outcome"] == "pass"
    assert result["coverage_ratio"] == 1
    assert result["mean"] == 4
    assert result["anomaly_propensity_score"] == 0
    missing = evaluate_series([], config, T - timedelta(minutes=15), T)
    assert missing["outcome"] == "unavailable"
    assert missing["value"] is None
    failed = evaluate_series(
        points([4, 100], T - timedelta(minutes=15)), config, T - timedelta(minutes=15), T
    )
    assert failed["outcome"] == "fail"
    assert failed["above_limit"] == 1
    assert any("stale" in r for r in failed["reasons"])


def test_duplicate_and_nonfinite_data_are_not_presented_as_totals():
    config = metric(aggregation="sum")
    rows = points([2, 3])
    rows.append(rows[0])
    rows.append({"timestamp": T.isoformat(), "value": float("nan")})
    result = evaluate_series(rows, config, T - timedelta(minutes=2), T)
    assert result["outcome"] == "fail"
    assert result["duplicate_count"] == 1
    assert result["value"] is None


def test_counter_rollover_withholds_quantity_and_rate():
    result = evaluate_series(
        points([100, 110, 2, 6]), metric(aggregation="counter"), T - timedelta(minutes=4), T
    )
    assert result["counter_resets"] == 1
    assert result["value"] is None
    assert result["rate_per_hour"] is None
    monotonic = evaluate_series(
        points([100, 110, 120]), metric(aggregation="counter"), T - timedelta(minutes=3), T
    )
    assert monotonic["outcome"] == "warn"
    assert monotonic["value"] == 20
    assert monotonic["rate_per_hour"] == 600


def test_week_month_quantity_comparison_normalizes_duration():
    current = {"outcome": "pass", "value": 1680, "rate_per_hour": 10}
    month = {"outcome": "pass", "value": 7440, "rate_per_hour": 10}
    comparison = compare_windows(current, month, "sum")
    assert comparison["percent_change"] == 0
    assert comparison["basis"] == "rate_per_hour"
    month["rate_per_hour"] = 0
    assert compare_windows(current, month, "sum")["percent_change"] is None
    month["outcome"] = "fail"
    assert compare_windows(current, month, "sum")["difference"] is None


def test_calendar_month_windows_respect_local_timezone():
    end = datetime(2026, 4, 2, 8, tzinfo=timezone.utc)
    windows = reporting_windows(end, "Europe/London")
    start, stop = windows["previous_calendar_month"]
    assert start == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert stop == datetime(2026, 3, 31, 23, tzinfo=timezone.utc)
    assert windows["current_week"][1] - windows["current_week"][0] == timedelta(days=7)


def test_invalid_metric_limits_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        metric(min=10, max=2)


def test_handover_uses_actual_wall_clock_shift_across_dst():
    from types import SimpleNamespace
    from app.services.operational_reports import current_reporting_window

    job = SimpleNamespace(
        task_type="handover",
        interval_minutes=480,
        timezone="Europe/London",
        config={"schedule_mode": "wall_clock", "schedule_anchor": "2026-03-28T22:00:00Z"},
    )
    start, end = current_reporting_window(job, datetime(2026, 3, 29, 5, tzinfo=timezone.utc))
    assert start == datetime(2026, 3, 28, 22, tzinfo=timezone.utc)
    assert end - start == timedelta(hours=7)


def test_handover_incidents_are_workspace_scoped_and_cover_shift_updates(db):
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4
    from app.models import User, Scope, Workspace, Membership, Incident, IncidentEvent
    from app.repositories import NotFound
    from app.services.operational_reports import OperationalReportService

    user = User(
        email="handover@example.test",
        name="Engineer Alpha",
        password_hash="unused",
        active=True,
        clearance=3,
    )
    scope = Scope(name="Handover plant", kind="plant")
    db.add_all([user, scope])
    db.flush()
    visible = Workspace(
        name="Visible unit", scope_id=scope.id, created_by=user.id, external_ai_enabled=True
    )
    secret = Workspace(
        name="Other unit", scope_id=scope.id, created_by=user.id, external_ai_enabled=True
    )
    db.add_all([visible, secret])
    db.flush()
    db.add(Membership(workspace_id=visible.id, user_id=user.id, role="member"))
    db.flush()
    stop = T
    start = stop - timedelta(hours=8)

    def incident(title, *, workspace=visible, status="open", created=None, updated=None):
        obj = Incident(
            workspace_id=workspace.id,
            title=title,
            summary=title + " details",
            status=status,
            severity="high",
            reported_by=user.id,
            assignee_id=user.id,
            request_id=str(uuid4()),
            created_at=created or start - timedelta(days=1),
            updated_at=updated or start - timedelta(days=1),
        )
        db.add(obj)
        db.flush()
        return obj

    old_open = incident("Unresolved from prior shift")
    recently_resolved = incident(
        "Resolved in this shift", status="resolved", updated=stop - timedelta(hours=1)
    )
    incident("Old resolved issue", status="resolved")
    incident("Future issue", created=stop + timedelta(hours=1), updated=stop + timedelta(hours=1))
    incident("OTHER UNIT SECRET", workspace=secret)
    event_updated = incident(
        "Activity in shift, later status", status="resolved", updated=stop + timedelta(minutes=30)
    )
    db.add(
        IncidentEvent(
            incident_id=event_updated.id,
            actor_id=user.id,
            action="acknowledged",
            detail={},
            created_at=start + timedelta(hours=1),
        )
    )
    db.commit()
    job = SimpleNamespace(
        task_type="handover",
        config={},
        workspace_id=visible.id,
        interval_minutes=480,
        timezone="UTC",
    )
    result = asyncio.run(OperationalReportService(db).execute(user, job, stop))
    handover = result.result_data["handover"]
    assert (
        result.result_data["outcome"] == "unavailable"
    )  # missing metrics cannot become a false pass
    assert result.result_data["metric_quality"] == "unavailable"
    assert handover["total"] == 3 and not handover["truncated"]
    assert {i["id"] for i in handover["incidents"]} == {
        old_open.id,
        recently_resolved.id,
        event_updated.id,
    }
    assert all(i["assignee"]["name"] == "Engineer Alpha" for i in handover["incidents"])
    assert "OTHER UNIT SECRET" not in result.body and "Old resolved issue" not in result.body
    assert any(
        i["id"] == event_updated.id and i["updated_after_shift"] for i in handover["incidents"]
    )
    assert {r["id"] for r in result.sources} == {
        old_open.id,
        recently_resolved.id,
        event_updated.id,
    }
    job.workspace_id = secret.id
    with pytest.raises(NotFound):
        asyncio.run(OperationalReportService(db).execute(user, job, stop))


def test_handover_incident_register_reports_truncation(db):
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4
    from app.models import User, Scope, Workspace, Membership, Incident
    from app.services.operational_reports import OperationalReportService

    user = User(
        email="handover-many@example.test",
        name="Shift owner",
        password_hash="unused",
        active=True,
        clearance=3,
    )
    scope = Scope(name="Unit", kind="unit")
    db.add_all([user, scope])
    db.flush()
    workspace = Workspace(
        name="Shift", scope_id=scope.id, created_by=user.id, external_ai_enabled=True
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="member"))
    for index in range(101):
        db.add(
            Incident(
                workspace_id=workspace.id,
                title=f"Open issue {index}",
                summary="Needs inspection",
                reported_by=user.id,
                request_id=str(uuid4()),
                created_at=T - timedelta(days=1),
                updated_at=T - timedelta(days=1),
            )
        )
    db.commit()
    job = SimpleNamespace(
        task_type="handover",
        config={"metrics": []},
        workspace_id=workspace.id,
        interval_minutes=480,
        timezone="UTC",
    )
    result = asyncio.run(OperationalReportService(db).execute(user, job, T))
    register = result.result_data["handover"]
    assert register["total"] == 101 and register["shown"] == 100 and register["truncated"]
    assert len(result.sources) == 100
    assert "Showing 100 of 101" in result.body
