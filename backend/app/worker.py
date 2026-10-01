"""PostgreSQL task/reminder worker.

Occurrences, reports, and notifications are idempotent. External inference is
at-least-once: a process can die after the provider accepted a request but before
committing its result. Expiring leases plus fencing prevent stale result writes.
No transaction or database lock is held while waiting for the model provider.
"""

import asyncio
import logging
import signal
import uuid
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    AuditEvent,
    JobDependency,
    JobRun,
    Membership,
    Notification,
    Reminder,
    Report,
    ScheduledJob,
    User,
    Workspace,
)
from app.repositories import AccessRepository, AuthorizationError, NotFound
from app.services.context import ContextService
from app.services.errors import ProviderError, ServiceError
from app.services.gateway import OpenRouterGateway
from app.services.jobs import (
    JobService,
    compatible_dependency,
    dependency_occurrence,
    require_execution_authorized,
    utcnow,
)

MAX_ATTEMPTS = 3
log = logging.getLogger("voptimai.worker")


@dataclass(frozen=True)
class Lease:
    run_id: str
    token: str
    request_id: str


class Worker:
    def __init__(
        self, session_factory: Callable = SessionLocal, settings: Any = None, gateway=None
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.gateway = gateway or OpenRouterGateway(self.settings)
        # Give bounded provider I/O enough time to finish before the lease expires.
        self.lease_seconds = max(
            self.settings.job_lease_seconds, self.settings.openrouter_timeout_seconds + 60
        )

    def schedule_due(self, now: datetime | None = None) -> int:
        """Catch up one occurrence per due task per tick; do not discard missed windows."""
        now = now or utcnow()
        with self.session_factory() as session, session.begin():
            jobs = session.scalars(
                select(ScheduledJob)
                .where(
                    ScheduledJob.status == "active",
                    ScheduledJob.next_run_at <= now,
                    or_(
                        ScheduledJob.activation_not_before.is_(None),
                        ScheduledJob.activation_not_before <= now,
                    ),
                    ScheduledJob.approval_status.in_(["legacy", "approved"]),
                )
                .order_by(ScheduledJob.next_run_at, ScheduledJob.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                scheduled_for = job.next_run_at
                JobService.insert_occurrence(session, job.id, scheduled_for)
                if job.interval_minutes is None:
                    # Already-queued occurrences of a completed one-shot task may execute.
                    job.status = "completed"
                else:
                    from app.services.schedules import next_occurrence

                    anchor = (
                        datetime.fromisoformat(job.config["schedule_anchor"])
                        if job.config.get("schedule_anchor")
                        else None
                    )
                    job.next_run_at = next_occurrence(
                        scheduled_for,
                        job.interval_minutes,
                        job.timezone,
                        mode=job.config.get("schedule_mode", "elapsed"),
                        anchor=anchor,
                    )
            return len(jobs)

    def notify_due_reminders(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        count = 0
        with self.session_factory() as session, session.begin():
            reminders = session.scalars(
                select(Reminder)
                .join(User, User.id == Reminder.owner_id)
                .where(
                    User.active.is_(True),
                    Reminder.due_at <= now,
                    Reminder.notified_at.is_(None),
                    Reminder.status.in_(["open", "waiting", "snoozed"]),
                )
                .order_by(Reminder.due_at, Reminder.id)
                .limit(100)
                .with_for_update(of=Reminder, skip_locked=True)
            ).all()
            for reminder in reminders:
                owner = session.scalar(
                    select(User).where(User.id == reminder.owner_id).with_for_update()
                )
                if owner is None or not owner.active:
                    continue
                self._notify(
                    session,
                    owner.id,
                    "reminder",
                    "Reminder due",
                    reminder.title,
                    "reminder",
                    reminder.id,
                    f"reminder:{reminder.id}:{reminder.due_at.isoformat()}",
                )
                reminder.notified_at = now
                count += 1
        return count

    def _dependencies(self, session, job, run, now: datetime) -> tuple[str, str | None, list[dict]]:
        results: list[dict] = []
        dependency_ids = session.scalars(
            select(JobDependency.depends_on_id).where(JobDependency.job_id == job.id)
        ).all()
        waiting = False
        for dependency_id in dependency_ids:
            dependency = session.get(ScheduledJob, dependency_id)
            if dependency is None or not compatible_dependency(job, dependency):
                return (
                    "blocked",
                    "A dependency is missing or has an incompatible audience or schedule.",
                    [],
                )
            try:
                from app.services.governance import GovernanceService

                GovernanceService(session).require_approved(dependency)
                if dependency.status == "paused":
                    return "blocked", "A dependency is paused.", []
            except (ServiceError, AuthorizationError, NotFound):
                return "blocked", "A dependency requires current authorization and approval.", []
            required_time = (
                run.scheduled_for
                if run.trigger_kind == "manual"
                else dependency_occurrence(job, dependency, run.scheduled_for)
            )
            if required_time is None:
                return "blocked", "A dependency has not started for this reporting window.", []
            dependency_run = session.scalar(
                select(JobRun).where(
                    JobRun.job_id == dependency_id, JobRun.scheduled_for == required_time
                )
            )
            if dependency_run is None:
                if dependency.status == "active" and dependency.next_run_at <= required_time <= now:
                    waiting = True  # Another scheduler tick will materialize this exact occurrence.
                    continue
                return "blocked", "A dependency has no run for this exact scheduled occurrence.", []
            if dependency_run.status in {"failed", "blocked"}:
                return (
                    "blocked",
                    "A dependency failed or was blocked for this scheduled occurrence.",
                    [],
                )
            if dependency_run.status != "succeeded":
                if dependency.status == "paused":
                    return "blocked", "A dependency is paused for this scheduled occurrence.", []
                waiting = True
                continue
            if (dependency_run.outcome or {}).get("job_revision", 1) != dependency.revision:
                return "blocked", "A dependency result belongs to a previous job revision.", []
            outcome = (dependency_run.outcome or {}).get("outcome", "pass")
            allowed = {"pass"} if job.dependency_policy == "pass" else {"pass", "warn"}
            if job.dependency_policy != "any" and outcome not in allowed:
                return (
                    "blocked",
                    f"A dependency reported {outcome}; policy {job.dependency_policy} does not permit it.",
                    [],
                )
            report = session.scalar(select(Report).where(Report.job_run_id == dependency_run.id))
            if report is None:
                return "blocked", "A completed dependency has no persisted report.", []
            if report.workspace_id != job.workspace_id or (
                job.workspace_id is None and report.owner_id != job.owner_id
            ):
                return "blocked", "A dependency report is outside this task's audience.", []
            results.append(
                {
                    "id": report.id,
                    "title": report.title,
                    "body": report.body,
                    "version": report.created_at.isoformat(),
                }
            )
        return ("waiting", None, []) if waiting else ("ready", None, results)

    def claim_one(self, now: datetime | None = None) -> Lease | None:
        now = now or utcnow()
        with self.session_factory() as session, session.begin():
            runs = session.scalars(
                select(JobRun)
                .where(
                    or_(
                        and_(JobRun.status == "queued", JobRun.available_at <= now),
                        and_(JobRun.status == "running", JobRun.lease_until <= now),
                    )
                )
                .order_by(JobRun.available_at, JobRun.scheduled_for, JobRun.id)
                .limit(25)
                .with_for_update(skip_locked=True)
            ).all()
            for run in runs:
                job = session.get(ScheduledJob, run.job_id)
                if job is None:
                    continue
                if job.activation_not_before and job.activation_not_before > now:
                    run.status = "queued"
                    run.available_at = job.activation_not_before
                    run.lease_until = run.lease_token = None
                    continue
                if run.attempts >= MAX_ATTEMPTS:
                    self._terminal(
                        session,
                        run,
                        "failed",
                        "The worker lease expired after the maximum number of attempts.",
                        now,
                    )
                    self._notify_failure_if_authorized(session, job, run)
                    continue
                state, reason, _ = self._dependencies(session, job, run, now)
                if state == "blocked":
                    self._terminal(
                        session, run, "blocked", reason or "Dependency unavailable.", now
                    )
                    self._notify_failure_if_authorized(session, job, run)
                    continue
                if state == "waiting":
                    run.status = "queued"
                    run.lease_until = run.lease_token = None
                    run.available_at = now + timedelta(seconds=self.settings.worker_poll_seconds)
                    continue
                token, request_id = str(uuid.uuid4()), str(uuid.uuid4())
                run.status = "running"
                run.attempts += 1
                run.lease_token = token
                run.lease_until = now + timedelta(seconds=self.lease_seconds)
                run.started_at = run.started_at or now
                run.finished_at = None
                session.add(
                    AuditEvent(
                        actor_id=job.owner_id,
                        action="job.run.started",
                        resource_type="job_run",
                        resource_id=run.id,
                        details={"request_id": request_id, "attempt": run.attempts},
                    )
                )
                return Lease(run.id, token, request_id)
        return None

    @staticmethod
    def _current_run(session, lease: Lease):
        return session.scalar(
            select(JobRun)
            .where(
                JobRun.id == lease.run_id,
                JobRun.status == "running",
                JobRun.lease_token == lease.token,
                JobRun.lease_until > utcnow(),
            )
            .with_for_update()
        )

    def _authorize(self, session, job):
        # These row locks fence revocation/configuration races through the result commit.
        owner = session.scalar(
            select(User).where(User.id == (job.execution_user_id or job.owner_id)).with_for_update()
        )
        if owner is None:
            raise ServiceError("The task owner is unavailable.", 403)
        if job.workspace_id:
            session.scalar(
                select(Workspace).where(Workspace.id == job.workspace_id).with_for_update()
            )
            session.scalar(
                select(Membership)
                .where(
                    Membership.workspace_id == job.workspace_id,
                    Membership.user_id == owner.id,
                )
                .with_for_update()
            )
        require_execution_authorized(session, owner, job, self.settings)
        return owner

    @staticmethod
    def _job_fingerprint(job):
        return tuple(
            getattr(job, key)
            for key in (
                "owner_id",
                "workspace_id",
                "instructions",
                "model",
                "external_ai_enabled",
                "execution_user_id",
                "task_type",
                "revision",
                "approved_revision",
                "approval_status",
                "timezone",
                "interval_minutes",
                "dependency_policy",
                "name",
            )
        ), json.dumps(job.config, sort_keys=True)

    async def _heartbeat(self, lease):
        while True:
            await asyncio.sleep(20)
            with self.session_factory() as session, session.begin():
                run = self._current_run(session, lease)
                if run is None:
                    return
                run.lease_until = utcnow() + timedelta(seconds=self.lease_seconds)

    async def execute(self, lease: Lease) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        try:
            with self.session_factory() as session, session.begin():
                run = self._current_run(session, lease)
                if run is None:
                    return
                job = session.scalar(
                    select(ScheduledJob).where(ScheduledJob.id == run.job_id).with_for_update()
                )
                principal = self._authorize(session, job)
                state, reason, dependencies = self._dependencies(session, job, run, utcnow())
                if state != "ready":
                    raise ServiceError(reason or "Dependencies are no longer ready.", 409)
                typed = job.task_type in {"validation", "handover", "production_comparison"}
                context = (
                    None
                    if typed
                    else ContextService(session, self.settings, reserve_chars=4500).for_job(
                        principal, job, dependencies
                    )
                )
                fingerprint = self._job_fingerprint(job)
                principal_id, job_id, workspace_id, model = (
                    principal.id,
                    job.id,
                    job.workspace_id,
                    job.model,
                )
                scheduled_for = run.scheduled_for
            if typed:
                from app.services.operational_reports import OperationalReportService

                with self.session_factory() as session:
                    operational = await OperationalReportService(session, self.settings).execute(
                        session.get(User, principal_id),
                        session.get(ScheduledJob, job_id),
                        scheduled_for,
                    )
                    body, sources, result_data = (
                        operational.body,
                        operational.sources,
                        operational.result_data,
                    )
                    metadata = {}
                    usage = {}
                    used_model = "deterministic"
            else:
                from app.agents.runtime import AgentRunner

                completion = await AgentRunner(
                    self.session_factory, self.settings, self.gateway
                ).run_bundle(
                    principal_id,
                    context,
                    workspace_id=workspace_id,
                    model=model,
                    request_id=lease.request_id,
                )
                body, sources, result_data = (
                    completion.content,
                    completion.sources,
                    {"outcome": "pass", "task_type": "analysis"},
                )
                metadata = completion.context_metadata
                usage = completion.usage
                used_model = completion.model
            with self.session_factory() as session, session.begin():
                run = self._current_run(session, lease)
                if run is None:
                    return
                job = session.scalar(
                    select(ScheduledJob).where(ScheduledJob.id == run.job_id).with_for_update()
                )
                principal = self._authorize(session, job)
                if self._job_fingerprint(job) != fingerprint:
                    raise ServiceError("The task configuration changed during execution.", 409)
                state, reason, _ = self._dependencies(session, job, run, utcnow())
                if state != "ready":
                    raise ServiceError(reason or "Dependency authorization changed.", 409)
                policy = AccessRepository(session)
                if not policy.source_refs_authorized(principal, sources, for_external_ai=not typed):
                    raise ServiceError(
                        "A source changed or access was revoked; the result was discarded.", 409
                    )
                if not typed:
                    ContextService(session, self.settings).validate_metadata(
                        principal, metadata, workspace_id
                    )
                result_data = {**result_data, "job_revision": job.revision}
                report = Report(
                    id=str(uuid.uuid4()),
                    owner_id=None if workspace_id else job.owner_id,
                    workspace_id=workspace_id,
                    job_run_id=run.id,
                    title=f"{job.name} — {run.scheduled_for.isoformat()}",
                    body=body,
                    source_refs=sources,
                    result_data=result_data,
                )
                session.add(report)
                session.flush()
                self._terminal(session, run, "succeeded", None, utcnow())
                run.result = body
                run.outcome = result_data
                session.flush()
                if job.config.get("alert_rule"):
                    from app.services.incidents import IncidentService

                    IncidentService(session).record_finding(principal, job, result_data, run.id)
                self._notify(
                    session,
                    job.owner_id,
                    "job_succeeded",
                    "Scheduled report ready",
                    job.name,
                    "report",
                    report.id,
                    f"job_run:{run.id}:succeeded",
                )
                # Explicitly configured outbound destinations are versioned with the job.
                if job.config.get("deliveries"):
                    from app.services.delivery import DeliveryService

                    accountable = session.get(User, job.owner_id)
                    for i, destination in enumerate(job.config["deliveries"][:10]):
                        DeliveryService(session).enqueue(
                            accountable,
                            channel=destination["channel"],
                            recipient=destination["recipient"],
                            subject=report.title,
                            body=report.body,
                            connection_id=destination.get("connection_id"),
                            request_id=f"job:{run.id}:{i}",
                            source_refs=[
                                {
                                    "type": "report",
                                    "id": report.id,
                                    "version": report.created_at.isoformat(),
                                }
                            ],
                        )
                session.add(
                    AuditEvent(
                        actor_id=principal.id,
                        action="job.run.succeeded",
                        resource_type="job_run",
                        resource_id=run.id,
                        details={
                            "request_id": lease.request_id,
                            "model": used_model,
                            "usage": usage,
                            "accountable_user_id": job.owner_id,
                        },
                    )
                )
        except (ServiceError, AuthorizationError, NotFound) as exc:
            self._record_failure(lease, exc)
        except Exception as exc:
            log.error(
                "Task failed internally: request_id=%s error_type=%s",
                lease.request_id,
                type(exc).__name__,
            )
            self._record_failure(
                lease, ServiceError("An internal worker error prevented task completion.", 500)
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    def _record_failure(self, lease: Lease, exc: Exception) -> None:
        with self.session_factory() as session, session.begin():
            run = self._current_run(session, lease)
            if run is None:
                return
            job = session.scalar(
                select(ScheduledJob).where(ScheduledJob.id == run.job_id).with_for_update()
            )
            detail = (
                exc.detail
                if isinstance(exc, ServiceError)
                else "The task owner no longer has the required access."
            )
            detail = f"{detail} Request {lease.request_id}."[:1000]
            authorized = True
            try:
                self._authorize(session, job)
            except (ServiceError, AuthorizationError, NotFound):
                authorized = False
            if (
                authorized
                and isinstance(exc, ProviderError)
                and exc.retryable
                and run.attempts < MAX_ATTEMPTS
            ):
                run.status = "queued"
                run.available_at = utcnow() + timedelta(
                    seconds=min(30 * (2 ** (run.attempts - 1)), 600)
                )
                run.lease_until = run.lease_token = None
                run.error = detail
                outcome = "retry"
            else:
                blocked = (
                    not authorized
                    or isinstance(exc, (AuthorizationError, NotFound))
                    or (isinstance(exc, ServiceError) and exc.status_code in {400, 403, 404, 409})
                )
                outcome = "blocked" if blocked else "failed"
                self._terminal(session, run, outcome, detail, utcnow())
                if authorized:
                    self._notify_failure_if_authorized(session, job, run)
                elif job.workspace_id and job.approval_status != "legacy":
                    job.status = "paused"
                    managers = session.scalars(
                        select(User)
                        .join(Membership, Membership.user_id == User.id)
                        .where(
                            Membership.workspace_id == job.workspace_id,
                            Membership.role == "manager",
                            User.active.is_(True),
                        )
                    )
                    for manager in managers:
                        try:
                            AccessRepository(session).job(manager, job.id)
                        except (AuthorizationError, NotFound):
                            continue
                        self._notify(
                            session,
                            manager.id,
                            "job_governance",
                            "Scheduled task paused",
                            "Review the accountable employee, approver and current access before resuming.",
                            "job",
                            job.id,
                            f"job:{job.id}:governance:{job.revision}:{manager.id}",
                        )
            session.add(
                AuditEvent(
                    actor_id=job.owner_id,
                    action=f"job.run.{outcome}",
                    resource_type="job_run",
                    resource_id=run.id,
                    details={
                        "request_id": lease.request_id,
                        "attempt": run.attempts,
                        "error_type": type(exc).__name__,
                    },
                )
            )

    @staticmethod
    def _terminal(session, run, status: str, error: str | None, now: datetime):
        run.status = status
        run.error = error
        run.finished_at = now
        run.lease_until = run.lease_token = None
        if status != "succeeded":
            run.result = None

    def _notify_failure_if_authorized(self, session, job, run):
        try:
            owner = self._authorize(session, job)
        except (ServiceError, AuthorizationError, NotFound):
            return
        self._notify(
            session,
            job.owner_id,
            "job_failed",
            "Scheduled task needs attention",
            job.name,
            "job",
            job.id,
            f"job_run:{run.id}:{run.status}",
        )

    @staticmethod
    def _notify(session, user_id, kind, title, body, resource_type, resource_id, dedupe_key):
        session.execute(
            insert(Notification)
            .values(
                id=str(uuid.uuid4()),
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                resource_type=resource_type,
                resource_id=resource_id,
                dedupe_key=dedupe_key,
            )
            .on_conflict_do_nothing(index_elements=[Notification.dedupe_key])
        )

    async def run_once(self) -> bool:
        self.schedule_due()
        self.notify_due_reminders()
        lease = self.claim_one()
        if lease:
            await self.execute(lease)
        return lease is not None


async def serve(role="all"):
    worker = Worker()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)

    async def lane(name):
        from app.agents.runtime import AgentRunner

        runner = AgentRunner(worker.session_factory, worker.settings, worker.gateway)
        next_retention = 0.0
        while not stopping.is_set():
            worked = False
            try:
                if name == "scheduler":
                    worker.schedule_due()
                    worker.notify_due_reminders()
                    if loop.time() >= next_retention:
                        runner.prune_checkpoints()
                        next_retention = loop.time() + 3600
                    from app.services.personal_routines import PersonalRoutineService

                    with worker.session_factory() as session, session.begin():
                        PersonalRoutineService(session).tick()
                elif name == "executor":
                    # Alternate bounded chat and scheduled work; neither queue starves.
                    agent_lease = runner.claim_one()
                    if agent_lease:
                        await runner.execute(agent_lease)
                        worked = True
                    lease = worker.claim_one()
                    if lease:
                        await worker.execute(lease)
                        worked = True
                elif name == "meetings":
                    from app.services.meetings import MeetingService

                    with worker.session_factory() as session:
                        worked = await MeetingService(session).process_one()
                elif name == "connectors":
                    from app.services.ingestion import KnowledgeIndexer
                    from app.services.calendar_sync import CalendarSyncService
                    from app.services.delivery import DeliveryService

                    await KnowledgeIndexer(
                        worker.session_factory, worker.settings, worker.gateway
                    ).run_once()
                    with worker.session_factory() as session:
                        await CalendarSyncService(session).tick()
                    with worker.session_factory() as session:
                        await DeliveryService(session).tick()
            except Exception as exc:
                log.error("Worker lane failed: role=%s error_type=%s", name, type(exc).__name__)
            if not worked:
                try:
                    await asyncio.wait_for(
                        stopping.wait(), timeout=worker.settings.worker_poll_seconds
                    )
                except TimeoutError:
                    pass

    roles = ["scheduler", "executor", "connectors", "meetings"] if role == "all" else [role]
    log.info("Worker started roles=%s", ",".join(roles))
    await asyncio.gather(*(lane(name) for name in roles))
    log.info("Worker stopped")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Third-party HTTP logging must not expose authorization headers or request payloads.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    import argparse

    parser = argparse.ArgumentParser(description="Run independently scalable durable worker lanes")
    parser.add_argument(
        "--role", choices=["all", "scheduler", "executor", "connectors", "meetings"], default="all"
    )
    asyncio.run(serve(parser.parse_args().role))


if __name__ == "__main__":
    main()
