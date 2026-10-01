"""Deterministic industrial reporting. Numbers never depend on model arithmetic.

Source metrics are explicitly configured and all query windows are half-open UTC
intervals. Aggregated data is labelled; heuristic deviation scores are not event
probabilities. No readings or thresholds are invented when configuration is absent.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.config import get_settings
from app.services.errors import ServiceError


class MetricConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=100)
    bucket_id: str = Field(min_length=1, max_length=200)
    measurement: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)
    unit: str = Field(default="", max_length=40)
    min: float | None = None
    max: float | None = None
    max_age_minutes: float = Field(default=15, gt=0, le=10080)
    expected_interval_seconds: float | None = Field(default=None, gt=0, le=86400)
    aggregation: Literal["mean", "sum", "counter"] = "mean"
    tags: dict[str, str] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def valid_limits(self):
        if any(value is not None and not math.isfinite(value) for value in (self.min, self.max)):
            raise ValueError("Metric limits must be finite")
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError("Metric minimum must be below maximum")
        return self


@dataclass(frozen=True)
class OperationalResult:
    body: str
    result_data: dict
    sources: list[dict]


def aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def reporting_windows(scheduled_for, timezone_name="UTC"):
    stop = aware(scheduled_for)
    local = stop.astimezone(ZoneInfo(timezone_name))
    month_end = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start = (month_end - timedelta(days=1)).replace(day=1)
    return {
        "current_week": (stop - timedelta(days=7), stop),
        "previous_week": (stop - timedelta(days=14), stop - timedelta(days=7)),
        "two_weeks_ago": (stop - timedelta(days=21), stop - timedelta(days=14)),
        "previous_calendar_month": (
            month_start.astimezone(timezone.utc),
            month_end.astimezone(timezone.utc),
        ),
    }


def current_reporting_window(job, scheduled_for):
    stop = aware(scheduled_for)
    config = job.config or {}
    default = 480 if job.task_type == "handover" else 15
    try:
        minutes = int(config.get("window_minutes", default))
    except (TypeError, ValueError) as exc:
        raise ServiceError("Reporting window must be an integer number of minutes.", 422) from exc
    if not 1 <= minutes <= 46080:
        raise ServiceError("Reporting window must be between 1 minute and 32 days.", 422)
    if (
        job.task_type == "handover"
        and config.get("schedule_mode") == "wall_clock"
        and job.interval_minutes == 480
    ):
        from app.services.schedules import previous_occurrence

        return (
            previous_occurrence(
                stop,
                job.interval_minutes,
                job.timezone,
                mode="wall_clock",
                anchor=config.get("schedule_anchor"),
            ),
            stop,
        )
    return stop - timedelta(minutes=minutes), stop


def evaluate_series(rows, metric: MetricConfig, start, stop, *, aggregate_minutes=None):
    start, stop = aware(start), aware(stop)
    points = []
    invalid = 0
    outside = 0
    for row in rows:
        try:
            timestamp = aware(row["timestamp"])
            value = row["value"]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or abs(value) > 1e100
            ):
                raise ValueError("Nonnumeric value")
            if not start <= timestamp < stop:
                outside += 1
                continue
            points.append((timestamp, float(value)))
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid += 1
    points.sort(key=lambda p: p[0])
    reasons = []
    outcome = "pass"
    result = {
        "outcome": outcome,
        "count": len(points),
        "invalid_count": invalid,
        "outside_window_count": outside,
        "aggregation": metric.aggregation,
        "aggregate_minutes": aggregate_minutes,
        "unit": metric.unit,
        "window": {"start": start.isoformat(), "end": stop.isoformat()},
        "reasons": reasons,
    }
    if not points:
        return {
            **result,
            "outcome": "unavailable",
            "reasons": ["No valid readings in the requested window."],
            "value": None,
            "rate_per_hour": None,
            "anomaly_propensity_score": None,
        }
    values = [p[1] for p in points]
    duplicate_count = len(points) - len({p[0] for p in points})
    if duplicate_count:
        outcome = "fail"
        reasons.append("Duplicate timestamps: select one unambiguous tag series before reporting.")
    if invalid or outside:
        outcome = "fail"
        reasons.append("Invalid values or out-of-window readings were returned.")
    age_minutes = max(0, (stop - points[-1][0]).total_seconds() / 60)
    # For aggregate windows the timestamp marks the start, not the most recent sample.
    effective_age = max(0, age_minutes - (aggregate_minutes or 0))
    if effective_age > metric.max_age_minutes:
        outcome = "fail"
        reasons.append("The most recent available reading is stale for this reporting window.")
    below = sum(v < metric.min for v in values) if metric.min is not None else 0
    above = sum(v > metric.max for v in values) if metric.max is not None else 0
    if below or above:
        outcome = "fail"
        reasons.append("Readings exceed the configured operating limits.")
    interval = aggregate_minutes * 60 if aggregate_minutes else metric.expected_interval_seconds
    expected = math.ceil((stop - start).total_seconds() / interval) if interval else None
    coverage = min(1, len(points) / expected) if expected else None
    if coverage is not None and coverage < 0.9:
        outcome = "fail"
        reasons.append("Fewer than 90% of expected sample windows are present.")
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values)
    scale = 1.4826 * mad or statistics.pstdev(values)
    max_z = max(abs(v - median) / scale for v in values) if scale else 0.0
    score = round(min(100, max(0, max_z - 3) * 20), 2)
    quantity = None
    resets = 0
    if metric.aggregation == "sum":
        quantity = math.fsum(values)
    elif metric.aggregation == "counter":
        if len(values) < 2:
            outcome = "unavailable"
            reasons.append("At least two counter observations are required.")
        else:
            differences = [b - a for a, b in zip(values, values[1:])]
            resets = sum(delta < 0 for delta in differences)
            if resets:
                outcome = "fail"
                reasons.append(
                    "Counter reset or rollover detected; total is withheld until reset semantics are configured."
                )
            else:
                quantity = math.fsum(differences)
                if outcome == "pass":
                    outcome = "warn"
                reasons.append(
                    "Counter change covers the first through last observed timestamps, not unobserved window boundaries."
                )
    hours = (stop - start).total_seconds() / 3600
    measured_hours = (points[-1][0] - points[0][0]).total_seconds() / 3600
    rate = (
        (quantity / measured_hours if measured_hours and quantity is not None else None)
        if metric.aggregation == "counter"
        else (quantity / hours if quantity is not None else None)
    )
    result.update(
        outcome=outcome,
        mean=statistics.fmean(values),
        minimum=min(values),
        maximum=max(values),
        population_stddev=statistics.pstdev(values),
        first_at=points[0][0].isoformat(),
        last_at=points[-1][0].isoformat(),
        latest_age_minutes=round(age_minutes, 3),
        duplicate_count=duplicate_count,
        below_limit=below,
        above_limit=above,
        coverage_ratio=coverage,
        coverage_basis="aggregate_windows" if aggregate_minutes else "raw_samples",
        expected_count=expected,
        counter_resets=resets,
        value=quantity if metric.aggregation != "mean" else statistics.fmean(values),
        rate_per_hour=rate,
        rate_window_hours=measured_hours if metric.aggregation == "counter" else hours,
        anomaly_propensity_score=score,
        score_method="Robust within-window deviation: clip((max robust z - 3) * 20, 0, 100); standard deviation fallback when MAD is zero.",
        score_interpretation="Heuristic deviation score, not a calibrated anomaly probability.",
    )
    if duplicate_count or invalid or outside:
        result["value"] = None
        result["rate_per_hour"] = None
    return result


def compare_windows(current, baseline, aggregation):
    # Totals across different durations are deliberately not treated as equivalent.
    key = "mean" if aggregation == "mean" else "rate_per_hour"
    current_value, baseline_value = current.get(key), baseline.get(key)
    if current.get("outcome") in {"fail", "unavailable"} or baseline.get("outcome") in {
        "fail",
        "unavailable",
    }:
        return {
            "basis": key,
            "difference": None,
            "percent_change": None,
            "reason": "Comparison withheld because one window did not pass data validation.",
        }
    if current_value is None or baseline_value is None:
        return {
            "basis": key,
            "difference": None,
            "percent_change": None,
            "reason": "Comparable evidence is unavailable.",
        }
    return {
        "basis": key,
        "difference": current_value - baseline_value,
        "percent_change": (
            (current_value - baseline_value) / abs(baseline_value) * 100 if baseline_value else None
        ),
        "reason": (
            "Percentage change is undefined for a zero baseline." if baseline_value == 0 else None
        ),
    }


class OperationalReportService:
    def __init__(self, db, settings=None, connection_service=None):
        from app.services.connections import ConnectionService

        self.db, self.settings = db, settings or get_settings()
        self.connections = connection_service or ConnectionService(db, self.settings)

    def _with_handover(self, result, user, job, stop):
        """Attach the same authorized operational register to every handover result.

        The register is a current snapshot. It does not reconstruct historical
        assignment/status from the scheduled reporting boundary. Evidence added
        after that boundary is explicitly labelled, never silently backdated.
        """
        if job.task_type != "handover":
            return result
        from sqlalchemy import and_, case, exists, func, or_, select
        from app.models import Incident, IncidentEvent, User, utcnow
        from app.repositories import AccessRepository

        start, end = current_reporting_window(job, stop)
        data = dict(result.result_data)
        data["window"] = {"start": start.isoformat(), "end": end.isoformat()}
        data["metric_quality"] = result.result_data["outcome"]
        captured_at = utcnow()
        if not job.workspace_id:
            section = {
                "status": "unavailable",
                "reason": "Select a workspace to include its incident register.",
                "incidents": [],
                "total": 0,
                "shown": 0,
                "truncated": False,
            }
            data.update(handover=section, outcome="unavailable")
            return OperationalResult(
                result.body + "\n\nHandover incidents unavailable: " + section["reason"],
                data,
                result.sources,
            )
        # Hierarchy, administrator status or a connection grant cannot widen this audience.
        access = AccessRepository(self.db)
        access.workspace(user, job.workspace_id)
        in_window_event = exists(
            select(IncidentEvent.id).where(
                IncidentEvent.incident_id == Incident.id,
                IncidentEvent.created_at >= start,
                IncidentEvent.created_at < end,
            )
        )
        updated_in_window = or_(
            and_(Incident.updated_at >= start, Incident.updated_at < end), in_window_event
        )
        selected = and_(
            Incident.workspace_id == job.workspace_id,
            Incident.created_at < end,
            or_(Incident.status.in_(["open", "acknowledged"]), updated_in_window),
        )
        severity_rank = case(
            {"critical": 0, "high": 1, "medium": 2, "low": 3}, value=Incident.severity, else_=4
        )
        query = (
            select(
                Incident,
                User.name.label("assignee_name"),
                updated_in_window.label("updated_in_window"),
                func.count().over().label("total"),
            )
            .outerjoin(User, User.id == Incident.assignee_id)
            .where(selected)
        )
        rows = self.db.execute(
            query.order_by(severity_rank, Incident.updated_at.desc(), Incident.id).limit(100)
        ).all()
        incidents, sources = [], list(result.sources)
        for incident, assignee_name, changed, _total in rows:
            incidents.append(
                {
                    "id": incident.id,
                    "title": incident.title,
                    "status": incident.status,
                    "severity": incident.severity,
                    "summary": incident.summary[:2000],
                    "decision": (incident.decision or "")[:1000],
                    "resolution": (incident.resolution or "")[:1000],
                    "assignee": (
                        {"id": incident.assignee_id, "name": assignee_name}
                        if incident.assignee_id
                        else None
                    ),
                    "created_at": incident.created_at.isoformat(),
                    "updated_at": incident.updated_at.isoformat(),
                    "unresolved": incident.status in {"open", "acknowledged"},
                    "updated_during_shift": bool(changed),
                    "updated_after_shift": incident.updated_at >= end,
                    "text_truncated": len(incident.summary) > 2000
                    or len(incident.decision or "") > 1000
                    or len(incident.resolution or "") > 1000,
                }
            )
            sources.append(
                {
                    "type": "incident",
                    "id": incident.id,
                    "title": incident.title,
                    "version": incident.updated_at.isoformat(),
                }
            )
        total = rows[0].total if rows else 0
        section = {
            "status": "available",
            "workspace_id": job.workspace_id,
            "snapshot_at": captured_at.isoformat(),
            "window": data["window"],
            "incidents": incidents,
            "total": total,
            "shown": len(incidents),
            "limit": 100,
            "truncated": total > len(incidents),
            "selection": "Currently unresolved incidents created before shift end, and incidents updated or with recorded activity during the shift.",
            "status_basis": "Current status and assignment at report generation; historical status is not reconstructed.",
        }
        data["handover"] = section
        lines = [
            result.body,
            "",
            "Incident handover",
            f"Shift: {start.isoformat()} to {end.isoformat()}; register snapshot: {captured_at.isoformat()}.",
            section["status_basis"],
            f"Showing {len(incidents)} of {total} relevant incidents.",
        ]
        if section["truncated"]:
            lines.append(
                "The register exceeds 100 items. Review the workspace incident list for remaining issues."
            )
        if not incidents:
            lines.append(
                "No unresolved incidents or recorded incident updates matched this shift window."
            )
        for incident in incidents:
            assignee = incident["assignee"]["name"] if incident["assignee"] else "Unassigned"
            lines.append(
                f"- {incident['title']} [{incident['id']}]: {incident['severity']}; {incident['status']}; owner: {assignee}."
            )
            lines.append("  " + incident["summary"][:600])
            if incident["decision"]:
                lines.append("  Decision: " + incident["decision"][:400])
            if incident["resolution"]:
                lines.append("  Resolution: " + incident["resolution"][:400])
            if incident["updated_after_shift"]:
                lines.append(
                    "  This entry includes a current update made after the scheduled shift end."
                )
        return OperationalResult("\n".join(lines), data, sources)

    async def execute(self, user, job, scheduled_for):
        from pydantic import ValidationError
        from app.models_connections import Connection

        task_type = job.task_type
        config = job.config or {}
        configured = config.get("metrics", [])
        stop = aware(scheduled_for)
        if task_type not in {"validation", "handover", "production_comparison"}:
            raise ServiceError("Unsupported deterministic report type.", 422)
        if not isinstance(configured, list) or not 1 <= len(configured) <= 20:
            return self._with_handover(
                OperationalResult(
                    "Metric report unavailable: configure 1–20 source metrics before running this task.",
                    {"task_type": task_type, "outcome": "unavailable", "metrics": []},
                    [],
                ),
                user,
                job,
                stop,
            )
        try:
            metrics = [MetricConfig.model_validate(m) for m in configured]
        except ValidationError:
            return self._with_handover(
                OperationalResult(
                    "Metric report unavailable: the metric configuration is invalid. Check sources, intervals, limits and aggregations.",
                    {"task_type": task_type, "outcome": "unavailable", "metrics": []},
                    [],
                ),
                user,
                job,
                stop,
            )
        windows = (
            reporting_windows(stop, getattr(job, "timezone", "UTC"))
            if task_type == "production_comparison"
            else {"current": current_reporting_window(job, stop)}
        )
        results = []
        sources = []
        for metric in metrics:
            item = {
                "label": metric.label or metric.field,
                "unit": metric.unit,
                "field": metric.field,
                "aggregation": metric.aggregation,
                "windows": {},
                "comparisons": {},
            }
            for name, (start, end) in windows.items():
                try:
                    # Reporting uses per-hour aggregates only for long comparison windows.
                    # A mean of hourly means is labelled as such, never a raw-sample weighted mean.
                    minutes = 60 if task_type == "production_comparison" else None
                    rows = await self.connections.query_series(
                        user,
                        metric.connection_id,
                        bucket_id=metric.bucket_id,
                        measurement=metric.measurement,
                        field=metric.field,
                        start=start,
                        stop=end,
                        limit=5000,
                        aggregate_minutes=minutes,
                        aggregation=(
                            "last" if metric.aggregation == "counter" else metric.aggregation
                        ),
                        tags=metric.tags,
                    )
                    summary = evaluate_series(rows, metric, start, end, aggregate_minutes=minutes)
                    if minutes and metric.aggregation == "mean":
                        summary["mean_basis"] = (
                            "Unweighted mean of populated hourly means; hourly sample counts are not available."
                        )
                    item["windows"][name] = summary
                    connection = self.db.get(Connection, metric.connection_id)
                    if connection:
                        sources.append(
                            {
                                "type": "connection",
                                "id": connection.id,
                                "title": connection.name,
                                "version": connection.updated_at.isoformat(),
                                "window_start": start.isoformat(),
                                "window_end": end.isoformat(),
                                "field": metric.field,
                                "measurement": metric.measurement,
                                "tags": metric.tags,
                                "read_only": True,
                            }
                        )
                except ServiceError as exc:
                    item["windows"][name] = {
                        "outcome": "unavailable",
                        "reasons": [exc.detail],
                        "window": {"start": start.isoformat(), "end": end.isoformat()},
                        "value": None,
                    }
            if task_type == "production_comparison":
                item["comparisons"] = {
                    name: compare_windows(
                        item["windows"]["current_week"], result, metric.aggregation
                    )
                    for name, result in item["windows"].items()
                    if name != "current_week"
                }
            results.append(item)
        outcomes = [
            window["outcome"] for metric in results for window in metric["windows"].values()
        ]
        outcome = next(
            (state for state in ("unavailable", "fail", "warn") if state in outcomes), "pass"
        )
        data = {
            "task_type": task_type,
            "outcome": outcome,
            "scheduled_for": stop.isoformat(),
            "window": {
                "start": next(iter(windows.values()))[0].isoformat(),
                "end": stop.isoformat(),
            },
            "metrics": results,
            "read_only": True,
            "score_is_probability": False,
        }
        lines = [
            f"{task_type.replace('_',' ').title()} — {outcome.upper()}",
            f"Reporting end: {stop.isoformat()}",
            "All values below are calculated directly from the configured source readings.",
        ]
        for metric in results:
            lines.extend(["", f"{metric['label']} ({metric['unit'] or 'unit not configured'})"])
            for name, result in metric["windows"].items():
                window = result["window"]
                lines.append(
                    f"- {name}: {window['start']} to {window['end']}; {result['outcome']}; value={result.get('value')}; samples={result.get('count',0)}."
                )
                if result.get("rate_per_hour") is not None:
                    lines.append(
                        f"  Normalized rate: {result['rate_per_hour']:.6g} {metric['unit']}/hour."
                    )
                lines.extend(f"  {reason}" for reason in result.get("reasons", []))
            for baseline, comparison in metric["comparisons"].items():
                change = comparison["percent_change"]
                lines.append(
                    f"- Compared with {baseline} on {comparison['basis']}: "
                    + (
                        f"{change:+.2f}%"
                        if change is not None
                        else comparison.get("reason") or "unavailable"
                    )
                )
        lines.extend(
            [
                "",
                "Deviation scores are heuristic indicators, not calibrated anomaly probabilities. Validate process interpretations against approved operating procedures.",
            ]
        )
        # Preserve each consumed connection configuration version; windows remain distinct provenance.
        return self._with_handover(
            OperationalResult("\n".join(lines), data, sources), user, job, stop
        )
