"""Task creation and idempotent occurrence enqueueing. Caller owns the transaction."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.models import JobDependency, JobRun, ScheduledJob, User
from app.repositories import AccessRepository
from app.services.errors import ServiceError
from app.services.gateway import select_model


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ServiceError("Schedule times must include a timezone.")
    return value.astimezone(timezone.utc)


def require_execution_authorized(session, user, job, settings: Any = None):
    settings = settings or get_settings()
    from app.services.governance import GovernanceService

    GovernanceService(session).require_approved(job)
    if not user.active or (job.execution_user_id or job.owner_id) != user.id:
        raise ServiceError("The task owner is inactive or unavailable.", 403)
    if job.status == "paused":
        raise ServiceError("The task is paused.", 409)
    needs_ai = job.task_type in {"analysis", "morning_brief"}
    if needs_ai and not settings.allow_external_ai:
        raise ServiceError("External AI is disabled for this installation.", 403)
    if needs_ai and not job.external_ai_enabled:
        raise ServiceError("External AI consent is disabled for this task.", 403)
    workspace = None
    if job.workspace_id:
        workspace = AccessRepository(session).workspace(
            user, job.workspace_id, roles={"member", "manager"}
        )
        if needs_ai and not workspace.external_ai_enabled:
            raise ServiceError("External AI is disabled for this workspace.", 403)
    if needs_ai:
        select_model(job.model, settings)
    return workspace


def _schedule_anchor(job):
    value = (job.config or {}).get("schedule_anchor")
    if value is None:
        # Migrated v1 elapsed schedules still expose their phase through next_run_at.
        return as_utc(job.next_run_at), False
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        return as_utc(parsed), True
    except (ValueError, TypeError, AttributeError) as exc:
        raise ServiceError("The dependency schedule anchor is invalid.", 422) from exc


def compatible_dependency(job, dependency) -> bool:
    """Authorize a temporal join; matching phases are not required for recurrence.

    A consumer may depend on the exact latest required occurrence of a faster
    schedule, including different timezone/cadence modes. One-shot dependencies
    retain the single aligned occurrence contract.
    """
    if job.owner_id != dependency.owner_id or job.workspace_id != dependency.workspace_id:
        return False
    parent_period, dependency_period = job.interval_minutes, dependency.interval_minutes
    if parent_period is None or dependency_period is None:
        if parent_period is not None or dependency_period is not None:
            return False
        return _schedule_anchor(job)[0] == _schedule_anchor(dependency)[0]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (parent_period, dependency_period)
    ):
        return False
    return dependency_period <= parent_period and parent_period % dependency_period == 0


def dependency_occurrence(job, dependency, parent_scheduled_for):
    """Exact required dependency instant at/before a scheduled parent, or None.

    This is a schedule join, never a lookup of the latest successful report.
    Worker callers must query this exact timestamp and enforce its outcome and
    definition revision. Manual invocations use their explicit shared timestamp
    instead, as recorded by JobRun.trigger_kind.
    """
    if not compatible_dependency(job, dependency):
        raise ServiceError("The dependency has an incompatible audience or recurrence.", 409)
    parent_time = as_utc(parent_scheduled_for)
    anchor, immutable = _schedule_anchor(dependency)
    if dependency.interval_minutes is None:
        return anchor if anchor <= parent_time else None
    if immutable and parent_time < anchor:
        return None
    period = timedelta(minutes=dependency.interval_minutes)
    mode = (dependency.config or {}).get("schedule_mode", "elapsed")
    if mode == "elapsed":
        return anchor + ((parent_time - anchor) // period) * period
    if mode != "wall_clock":
        raise ServiceError("The dependency schedule mode is invalid.", 422)
    from app.services.schedules import next_occurrence, previous_occurrence

    candidate = previous_occurrence(
        parent_time + timedelta(microseconds=1),
        dependency.interval_minutes,
        dependency.timezone,
        mode,
        anchor=anchor,
    )
    if immutable and candidate < anchor:
        candidate = anchor
    # During the second half of a DST fold, a later nominal wall time can already
    # have occurred in UTC. Advance until the next exact recurrence is in future.
    for _ in range(10000):
        following = next_occurrence(
            candidate, dependency.interval_minutes, dependency.timezone, mode, anchor=anchor
        )
        if following > parent_time:
            return candidate if candidate <= parent_time else None
        candidate = following
    raise ServiceError("The dependency occurrence could not be resolved.", 422)


def validate_job_config(session, user, task_type, config, workspace_id=None):
    """Validate typed instructions at configuration time, before manager approval."""
    import json
    from pydantic import ValidationError

    if not isinstance(config, dict) or len(json.dumps(config)) > 32000:
        raise ServiceError("Task configuration must be a JSON object below 32 KB.")
    if task_type in {"validation", "handover", "production_comparison"}:
        from app.services.operational_reports import MetricConfig
        from app.repositories.connections import ConnectionRepository

        metrics = config.get("metrics", [])
        if not isinstance(metrics, list) or len(metrics) > 20:
            raise ServiceError("Configure at most 20 metrics.")
        for raw in metrics:
            try:
                metric = MetricConfig.model_validate(raw)
            except ValidationError:
                raise ServiceError(
                    "A metric has invalid fields, limits, sampling interval or aggregation.", 422
                )
            connection = ConnectionRepository(session).get(user, metric.connection_id)
            if connection.provider != "influxdb":
                raise ServiceError(
                    "Operational metrics require a read-only process data connection."
                )
            if workspace_id and connection.workspace_id != workspace_id:
                raise ServiceError(
                    "Workspace jobs must use connections shared with that same workspace.", 403
                )
        hours = config.get("window_hours", 8)
        if isinstance(hours, bool) or not isinstance(hours, (float, int)) or not 0 < hours <= 744:
            raise ServiceError("Report window_hours must be greater than zero and at most 744.")
    if config.get("schedule_mode", "elapsed") not in {"elapsed", "wall_clock"}:
        raise ServiceError("schedule_mode must be elapsed or wall_clock.")
    if "schedule_anchor" in config:
        try:
            as_utc(datetime.fromisoformat(config["schedule_anchor"].replace("Z", "+00:00")))
        except (ValueError, TypeError, AttributeError):
            raise ServiceError("schedule_anchor must be an ISO timestamp with a timezone.", 422)

    deliveries = config.get("deliveries", [])
    if not isinstance(deliveries, list) or len(deliveries) > 10:
        raise ServiceError("Configure at most ten delivery destinations.")
    for target in deliveries:
        if not isinstance(target, dict) or target.get("channel") not in {"email", "teams"}:
            raise ServiceError("Invalid delivery destination.")
        if target["channel"] == "email":
            from app.services.delivery import email_address

            email_address(target.get("recipient", ""))
        else:
            from app.repositories.connections import ConnectionRepository

            connection = ConnectionRepository(session).get(user, target.get("connection_id"))
            if connection.provider != "teams_workflow" or (
                workspace_id and connection.workspace_id != workspace_id
            ):
                raise ServiceError(
                    "Choose a Teams Workflows connection belonging to this workspace."
                )
    if "alert_rule" in config:
        from app.services.incidents import validate_alert_rule

        validate_alert_rule(config["alert_rule"])


class JobService:
    def __init__(self, session, settings: Any = None):
        self.session = session
        self.settings = settings or get_settings()
        self.access = AccessRepository(session)

    def create(
        self,
        user,
        *,
        name: str,
        instructions: str,
        model: str | None = None,
        workspace_id: str | None = None,
        interval_minutes: int | None = None,
        next_run_at: datetime | None = None,
        external_ai_enabled: bool = False,
        dependency_ids: Iterable[str] = (),
        task_type: str = "analysis",
        config: dict | None = None,
        timezone: str = "UTC",
        dependency_policy: str = "pass_warn",
    ) -> ScheduledJob:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        from app.services.governance import GovernanceService

        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ServiceError("Select a valid IANA timezone.")
        if task_type not in {
            "analysis",
            "validation",
            "handover",
            "production_comparison",
            "morning_brief",
        }:
            raise ServiceError("Unknown task type.")
        if dependency_policy not in {"pass", "pass_warn", "any"}:
            raise ServiceError("Unknown dependency outcome policy.")
        if (
            not isinstance(config or {}, dict)
            or len(__import__("json").dumps(config or {})) > 32000
        ):
            raise ServiceError("Task configuration must be a JSON object below 32 KB.")
        if not user.active:
            raise ServiceError("The user account is inactive.", 403)
        if not name.strip() or len(name) > 160:
            raise ServiceError("Task names must contain between 1 and 160 characters.")
        if not instructions.strip() or len(instructions) > 12000:
            raise ServiceError("Task instructions must contain between 1 and 12000 characters.")
        if interval_minutes is not None and (
            isinstance(interval_minutes, bool) or not 15 <= interval_minutes <= 20160
        ):
            raise ServiceError("The repeat interval must be between 15 and 20160 minutes.")
        if workspace_id:
            workspace = self.access.workspace(user, workspace_id, roles={"member", "manager"})
            if external_ai_enabled and not workspace.external_ai_enabled:
                raise ServiceError("External AI is disabled for this workspace.", 403)
        if external_ai_enabled and not self.settings.allow_external_ai:
            raise ServiceError("External AI is disabled for this installation.", 403)
        config = dict(config or {})
        initial_run = as_utc(next_run_at or utcnow())
        config["schedule_anchor"] = initial_run.isoformat()
        config.setdefault(
            "schedule_mode",
            (
                "wall_clock"
                if task_type in {"handover", "morning_brief", "production_comparison"}
                else "elapsed"
            ),
        )
        validate_job_config(self.session, user, task_type, config, workspace_id)
        job = ScheduledJob(
            id=str(uuid.uuid4()),
            owner_id=user.id,
            workspace_id=workspace_id,
            name=name.strip(),
            instructions=instructions.strip(),
            model=(
                select_model(model, self.settings)
                if task_type in {"analysis", "morning_brief"}
                else (model or self.settings.openrouter_default_model or "deterministic")
            ),
            interval_minutes=interval_minutes,
            next_run_at=initial_run,
            status="paused" if workspace_id else "active",
            external_ai_enabled=external_ai_enabled,
            task_type=task_type,
            config=config or {},
            timezone=timezone,
            dependency_policy=dependency_policy,
            approval_status="pending" if workspace_id else "approved",
            revision=1,
            approved_revision=None if workspace_id else 1,
        )
        self.session.add(job)
        self.session.flush()
        self.update_dependencies(user, job, dependency_ids)
        if workspace_id:
            GovernanceService(self.session).submit(user, job)
        return job

    def update_dependencies(self, user, job, dependency_ids: Iterable[str]) -> None:
        job = self.access.job(user, job.id)
        if job.owner_id != user.id:
            raise ServiceError("Only the task owner can configure dependencies.", 403)
        requested = list(dict.fromkeys(str(item) for item in dependency_ids))
        if len(requested) > 20:
            raise ServiceError("A task may have at most 20 dependencies.")
        edges = self.session.execute(
            select(JobDependency.job_id, JobDependency.depends_on_id)
        ).all()
        graph: dict[str, list[str]] = {}
        for left, right in edges:
            graph.setdefault(left, []).append(right)
        for dependency_id in requested:
            dependency = self.access.job(user, dependency_id)
            if not compatible_dependency(job, dependency):
                raise ServiceError(
                    "Dependencies must share an accountable owner and audience; their recurring intervals must divide the parent interval."
                )
            stack = [dependency_id]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current == job.id:
                    raise ServiceError("Task dependencies cannot contain a cycle.")
                if current not in visited:
                    visited.add(current)
                    stack.extend(graph.get(current, []))
        existing = self.session.scalars(
            select(JobDependency).where(JobDependency.job_id == job.id)
        ).all()
        for edge in existing:
            self.session.delete(edge)
        self.session.flush()
        for dependency_id in requested:
            self.session.add(JobDependency(job_id=job.id, depends_on_id=dependency_id))
        self.session.flush()

    def enqueue(self, user, job, scheduled_for: datetime | None = None) -> JobRun:
        """Manual execution requests a fresh dependency graph at one explicit instant."""
        occurrence = as_utc(scheduled_for or utcnow())
        visiting: set[str] = set()
        visited: dict[str, JobRun] = {}

        def enqueue_one(candidate) -> JobRun:
            candidate = self.access.job(user, candidate.id)
            if candidate.owner_id != user.id:
                if not candidate.workspace_id:
                    raise ServiceError("Only the task owner can run this task.", 403)
                self.access.workspace(user, candidate.workspace_id, roles={"manager"})
            execution_user = self.session.get(
                User, candidate.execution_user_id or candidate.owner_id
            )
            require_execution_authorized(self.session, execution_user, candidate, self.settings)
            if candidate.id in visiting:
                raise ServiceError("Task dependencies contain a cycle.")
            if candidate.id in visited:
                return visited[candidate.id]
            visiting.add(candidate.id)
            dependency_ids = self.session.scalars(
                select(JobDependency.depends_on_id).where(JobDependency.job_id == candidate.id)
            ).all()
            for dependency_id in dependency_ids:
                dependency = self.access.job(user, dependency_id)
                if not compatible_dependency(candidate, dependency):
                    raise ServiceError(
                        "A task dependency has an incompatible audience or schedule.", 409
                    )
                enqueue_one(dependency)
            visiting.remove(candidate.id)
            run = self.insert_occurrence(
                self.session, candidate.id, occurrence, trigger_kind="manual"
            )
            visited[candidate.id] = run
            return run

        return enqueue_one(job)

    @staticmethod
    def insert_occurrence(
        session, job_id: str, scheduled_for: datetime, trigger_kind: str = "scheduled"
    ) -> JobRun:
        if trigger_kind not in {"scheduled", "manual"}:
            raise ServiceError("Invalid job trigger kind.", 422)
        occurrence = as_utc(scheduled_for)
        run_id = str(uuid.uuid4())
        session.execute(
            insert(JobRun)
            .values(
                id=run_id,
                job_id=job_id,
                scheduled_for=occurrence,
                status="queued",
                attempts=0,
                available_at=utcnow(),
                trigger_kind=trigger_kind,
            )
            .on_conflict_do_nothing(index_elements=[JobRun.job_id, JobRun.scheduled_for])
        )
        result = session.scalars(
            select(JobRun).where(JobRun.job_id == job_id, JobRun.scheduled_for == occurrence)
        ).one()
        if trigger_kind == "manual" and result.trigger_kind != "manual":
            raise ServiceError(
                "A scheduled execution already exists at that exact instant. Run again with a new manual request.",
                409,
            )
        return result
