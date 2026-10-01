"""One incident lifecycle for people and approved deterministic validators.

Caller owns the transaction. IncidentEvent is the durable publication record;
notifications are projections delivered only to currently authorized subscribers.
"""

import hashlib
import json
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import models as m
from app.repositories import AccessRepository, AuthorizationError, NotFound
from app.services.errors import ServiceError


def notify(db, user_id, kind, title, body, resource_type, resource_id, dedupe_key):
    db.execute(
        insert(m.Notification)
        .values(
            id=str(uuid4()),
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=[m.Notification.dedupe_key])
    )


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    severity: Literal["low", "medium", "high", "critical"] = "high"
    publish: bool = False
    escalate: bool = False
    consecutive_failures: int = Field(default=2, ge=1, le=20)
    assignee_id: str | None = Field(default=None, max_length=36)
    outcomes: list[Literal["fail", "unavailable"]] = Field(
        default_factory=lambda: ["fail", "unavailable"], min_length=1, max_length=2
    )

    @field_validator("assignee_id")
    @classmethod
    def valid_assignee(cls, value):
        return str(UUID(value)) if value else None

    @model_validator(mode="after")
    def valid_escalation(self):
        if self.escalate and (not self.publish or self.severity != "critical"):
            raise ValueError("Escalation requires a published critical finding")
        return self


def validate_alert_rule(value):
    try:
        return AlertRule.model_validate(value or {})
    except ValidationError as exc:
        raise ServiceError(
            "Invalid alert rule: choose a valid severity, 1–20 consecutive failures and explicit publication/escalation settings.",
            422,
        ) from exc


class IncidentService:
    def __init__(self, db):
        self.db, self.access = db, AccessRepository(db)

    def _audit(self, user, action, incident_id, details=None):
        self.db.add(
            m.AuditEvent(
                actor_id=user.id,
                action=action,
                resource_type="incident",
                resource_id=incident_id,
                details=details or {},
            )
        )

    def _assignee(self, assignee_id, workspace_id):
        assignee = self.db.get(m.User, assignee_id) if assignee_id else None
        if assignee is None or not assignee.active:
            raise ServiceError(
                "Choose an active assignee who can contribute to this workspace.", 422
            )
        try:
            self.access.workspace(assignee, workspace_id, roles={"member", "manager"})
        except (NotFound, AuthorizationError) as exc:
            raise ServiceError(
                "Choose an active assignee who can contribute to this workspace.", 422
            ) from exc
        return assignee

    def publication_notifications(self, incident, event):
        workspace = self.db.get(m.Workspace, incident.workspace_id)
        scope = self.db.get(m.Scope, workspace.scope_id)
        interested_scopes, seen = {scope.id}, {scope.id}
        while scope.parent_id and scope.parent_id not in seen:
            interested_scopes.add(scope.parent_id)
            seen.add(scope.parent_id)
            scope = self.db.get(m.Scope, scope.parent_id)
            if scope is None or not incident.escalated:
                break
        subscribers = set(
            self.db.scalars(
                select(m.Subscription.user_id).where(m.Subscription.scope_id.in_(interested_scopes))
            )
        )
        reviewers = set(
            self.db.scalars(
                select(m.ScopeGrant.user_id).where(
                    m.ScopeGrant.scope_id.in_(interested_scopes), m.ScopeGrant.can_review.is_(True)
                )
            )
        )
        candidates = subscribers | reviewers
        if not candidates:
            return
        for user in self.db.scalars(
            select(m.User).where(m.User.id.in_(candidates), m.User.active.is_(True))
        ):
            try:
                publication = self.access.publication(user, incident.id)
            except (NotFound, AuthorizationError):
                continue
            notify(
                self.db,
                user.id,
                "publication_review" if user.id in reviewers else "publication",
                (
                    "Critical summary escalated"
                    if incident.escalated
                    else "Published summary available"
                ),
                publication["summary"],
                "publication",
                incident.id,
                f"publication:{incident.id}:{event}:{user.id}",
            )

    def assignee_notification(self, incident, event):
        user = self.db.get(m.User, incident.assignee_id) if incident.assignee_id else None
        if user is None or not user.active:
            return
        try:
            self.access.incident(user, incident.id)
        except (NotFound, AuthorizationError):
            return
        notify(
            self.db,
            user.id,
            "incident",
            "Incident assigned to you",
            incident.summary,
            "incident",
            incident.id,
            f"incident:{incident.id}:{event}:{user.id}",
        )

    def create(
        self,
        user,
        *,
        workspace_id,
        title,
        summary,
        severity,
        assignee_id,
        decision,
        request_id,
        published=False,
        note_id=None,
    ):
        workspace = self.access.workspace(user, workspace_id, roles={"member", "manager"})
        membership = self.db.get(m.Membership, (workspace.id, user.id))
        if published and membership.role != "manager":
            raise ServiceError("Only a workspace manager can publish a summary.", 403)
        self._assignee(assignee_id, workspace.id)
        if note_id:
            note = self.access.note(user, note_id)
            if summary not in note.body:
                raise ServiceError(
                    "A private-note summary must be the exact selected excerpt.", 422
                )
        # Private source IDs are intentionally excluded from shared records and audit.
        values = dict(
            workspace_id=workspace_id,
            title=title,
            summary=summary,
            severity=severity,
            assignee_id=assignee_id,
            decision=decision,
            request_id=request_id,
            published=published,
        )
        inserted = self.db.scalar(
            insert(m.Incident)
            .values(id=str(uuid4()), reported_by=user.id, **values)
            .on_conflict_do_nothing(index_elements=[m.Incident.workspace_id, m.Incident.request_id])
            .returning(m.Incident.id)
        )
        incident = self.db.scalar(
            select(m.Incident).where(
                m.Incident.workspace_id == workspace_id, m.Incident.request_id == request_id
            )
        )
        if inserted is None:
            if incident.reported_by != user.id or any(
                getattr(incident, key) != value for key, value in values.items()
            ):
                raise ServiceError(
                    "This request identifier was already used for a different incident.", 409
                )
            return incident
        self.db.add(
            m.IncidentEvent(
                incident_id=incident.id,
                actor_id=user.id,
                action="created",
                detail={"published": published},
            )
        )
        self._audit(user, "incident_created", incident.id)
        self.assignee_notification(incident, "created")
        if published:
            self.publication_notifications(incident, "published")
        self.db.flush()
        return incident

    def transition(self, user, incident_id, *, action, reason=""):
        incident = self.access.incident(user, incident_id, roles={"member", "manager"})
        incident = self.db.scalar(
            select(m.Incident).where(m.Incident.id == incident.id).with_for_update()
        )
        membership = self.db.get(m.Membership, (incident.workspace_id, user.id))
        if action in {"publish", "escalate"}:
            if membership.role != "manager":
                raise ServiceError(
                    "Only a workspace manager can publish or escalate summaries.", 403
                )
        elif user.id != incident.assignee_id and membership.role != "manager":
            raise ServiceError(
                "Only the assignee or a workspace manager can acknowledge or resolve this incident.",
                403,
            )
        changed = False
        if action == "acknowledge":
            if incident.status == "resolved":
                raise ServiceError("This incident is already resolved.", 409)
            if incident.status == "open":
                incident.status, changed = "acknowledged", True
        elif action == "resolve":
            if not reason:
                raise ServiceError("Describe the resolution or outcome.", 422)
            if incident.status != "resolved":
                incident.status, incident.resolution, changed = "resolved", reason, True
        elif action == "publish":
            if not incident.published:
                incident.published, changed = True, True
        elif action == "escalate":
            if incident.severity != "critical" or not incident.published:
                raise ServiceError("Only a published critical incident can be escalated.", 409)
            if not incident.escalated:
                incident.escalated, changed = True, True
        else:
            raise ServiceError("Unknown incident action.", 422)
        if changed:
            self.db.add(
                m.IncidentEvent(
                    incident_id=incident.id,
                    actor_id=user.id,
                    action=action,
                    detail={"reason": reason} if reason else {},
                )
            )
            self._audit(user, "incident_" + action, incident.id)
            self.db.flush()
            if action in {"publish", "escalate"}:
                self.publication_notifications(incident, action)
        return incident

    @staticmethod
    def _metric_failed(data, index, rule):
        metrics = data.get("metrics", [])
        if index >= len(metrics):
            return False
        current = metrics[index].get("windows", {}).get("current", {})
        return current.get("outcome") in rule.outcomes

    def record_finding(self, principal, job, result_data, run_id):
        """Publish only under the exact standing instruction approved by a manager.

        The locked job serializes incident episodes. A retry for the same run does
        not append another event or re-notify. A resolved episode stays resolved;
        a later run may start a fresh episode when the consecutive gate is met.
        """
        from app.services.governance import GovernanceService
        from app.models_governance import JobRevision

        if job.task_type != "validation" or not job.workspace_id:
            return []
        rule = validate_alert_rule((job.config or {}).get("alert_rule"))
        if not rule.enabled:
            return []
        job = self.db.scalar(
            select(m.ScheduledJob).where(m.ScheduledJob.id == job.id).with_for_update()
        )
        if job.approval_status != "approved" or job.approved_revision != job.revision:
            raise ServiceError("Automatic findings require an approved job revision.", 403)
        GovernanceService(self.db).require_approved(job)
        revision = self.db.scalar(
            select(JobRevision).where(
                JobRevision.job_id == job.id, JobRevision.revision == job.revision
            )
        )
        if principal.id != (job.execution_user_id or job.owner_id):
            raise ServiceError("The finding principal does not own this execution.", 403)
        self.access.workspace(principal, job.workspace_id, roles={"member", "manager"})
        assignee = self._assignee(rule.assignee_id or job.owner_id, job.workspace_id)
        run = self.db.get(m.JobRun, run_id)
        if not run or run.job_id != job.id:
            raise ServiceError("The finding is not linked to this job execution.", 422)
        report = self.db.scalar(select(m.Report).where(m.Report.job_run_id == run_id))
        if (
            report is None
            or report.workspace_id != job.workspace_id
            or report.result_data != result_data
        ):
            raise ServiceError(
                "Persist the matching validated report before recording a finding.", 409
            )
        previous = list(
            self.db.scalars(
                select(m.JobRun)
                .where(m.JobRun.job_id == job.id, m.JobRun.scheduled_for < run.scheduled_for)
                .order_by(m.JobRun.scheduled_for.desc())
                .limit(rule.consecutive_failures - 1)
            )
        )
        if len(previous) < rule.consecutive_failures - 1:
            return []
        configured = job.config.get("metrics", [])
        if not isinstance(configured, list):
            raise ServiceError("Configure source metrics before enabling findings.", 422)
        incidents = []
        for index, metric in enumerate(configured[:20]):
            if not self._metric_failed(result_data, index, rule):
                continue
            latest_time, consecutive = run.scheduled_for, True
            for earlier in previous:
                # An execution failure, pass, unavailable evidence, or missing occurrence
                # cannot be skipped to join two unrelated failures across a long gap.
                if earlier.status != "succeeded" or not self._metric_failed(
                    earlier.outcome or {}, index, rule
                ):
                    consecutive = False
                    break
                if revision.reviewed_at and earlier.scheduled_for < revision.reviewed_at:
                    consecutive = False
                    break
                if job.interval_minutes and latest_time - earlier.scheduled_for > timedelta(
                    minutes=job.interval_minutes * 1.5
                ):
                    consecutive = False
                    break
                latest_time = earlier.scheduled_for
            if not consecutive:
                continue
            signature = hashlib.sha256(
                json.dumps(
                    {
                        "job": job.id,
                        "metric": {
                            key: metric.get(key)
                            for key in (
                                "connection_id",
                                "bucket_id",
                                "measurement",
                                "field",
                                "tags",
                            )
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()[:40]
            prefix = f"finding:{signature}:"
            replay = self.db.scalar(
                select(m.Incident)
                .join(m.IncidentEvent, m.IncidentEvent.incident_id == m.Incident.id)
                .where(
                    m.Incident.workspace_id == job.workspace_id,
                    m.Incident.request_id.startswith(prefix),
                    m.IncidentEvent.detail["run_id"].astext == run_id,
                )
                .limit(1)
            )
            if replay:
                incidents.append(replay)
                continue
            latest = self.db.scalar(
                select(m.Incident)
                .where(
                    m.Incident.workspace_id == job.workspace_id,
                    m.Incident.request_id.startswith(prefix),
                )
                .order_by(m.Incident.created_at.desc(), m.Incident.id)
                .limit(1)
                .with_for_update()
            )
            incident = latest if latest and latest.status != "resolved" else None
            item = result_data["metrics"][index]
            current = item["windows"]["current"]
            label = str(item.get("label") or metric.get("field") or "Metric")[:200]
            reasons = [str(reason)[:500] for reason in current.get("reasons", [])[:8]]
            summary = (
                f"{label}: {current['outcome']}. {rule.consecutive_failures} consecutive validation windows triggered the approved rule. "
                + " ".join(reasons)
            )
            # Summaries deliberately omit credentials and raw data. Source report/run
            # evidence remains accessible only through normal workspace authorization.
            detail = {
                "run_id": run_id,
                "report_id": report.id,
                "job_id": job.id,
                "job_revision": job.revision,
                "metric_index": index,
                "outcome": current["outcome"],
                "consecutive_failures": rule.consecutive_failures,
            }
            created = incident is None
            if created:
                episode = latest.id if latest else "initial"
                incident = m.Incident(
                    workspace_id=job.workspace_id,
                    title=f"Validation finding: {label}"[:300],
                    summary=summary[:6000],
                    severity=rule.severity,
                    status="open",
                    assignee_id=assignee.id,
                    reported_by=principal.id,
                    decision="Review the validation evidence and record the outcome.",
                    published=rule.publish,
                    escalated=rule.escalate,
                    request_id=prefix + hashlib.sha256(episode.encode()).hexdigest()[:20],
                )
                self.db.add(incident)
                self.db.flush()
            else:
                incident.summary = summary[:6000]
                # Existing human acknowledgement, assignee, publication and severity
                # decisions are preserved. Automatic observations do not overwrite them.
            self.db.add(
                m.IncidentEvent(
                    incident_id=incident.id,
                    actor_id=principal.id,
                    action="agent_finding_created" if created else "agent_finding_observed",
                    detail=detail,
                )
            )
            self._audit(principal, "incident_agent_finding", incident.id, detail)
            self.db.flush()
            if created:
                self.assignee_notification(incident, "created")
                if incident.published:
                    self.publication_notifications(incident, "created")
            incidents.append(incident)
        return incidents
