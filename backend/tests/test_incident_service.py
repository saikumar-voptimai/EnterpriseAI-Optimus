"""Approved validator findings, consecutive evidence, episodes and publications."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import models as m
from app.models_governance import JobRevision
from app.models_connections import Connection
from app.services.errors import ServiceError
from app.services.governance import GovernanceService
from app.services.incidents import IncidentService, validate_alert_rule
from app.services.jobs import JobService


def setup(db, *, publish=False, escalate=False, consecutive=2):
    manager = m.User(
        name="Manager",
        email="manager@incidents.test",
        password_hash="test",
        active=True,
        clearance=3,
    )
    engineer = m.User(
        name="Engineer",
        email="engineer@incidents.test",
        password_hash="test",
        active=True,
        clearance=3,
    )
    parent = m.Scope(name="Plant", kind="plant")
    db.add_all([manager, engineer, parent])
    db.flush()
    unit = m.Scope(name="Unit", kind="unit", parent_id=parent.id)
    db.add(unit)
    db.flush()
    workspace = m.Workspace(
        name="Operations", scope_id=unit.id, created_by=manager.id, classification=2
    )
    db.add(workspace)
    db.flush()
    db.add_all(
        [
            m.Membership(workspace_id=workspace.id, user_id=manager.id, role="manager"),
            m.Membership(workspace_id=workspace.id, user_id=engineer.id, role="member"),
            m.ScopeGrant(user_id=manager.id, scope_id=parent.id, can_review=True),
        ]
    )
    db.flush()
    connection = Connection(
        owner_id=manager.id,
        workspace_id=workspace.id,
        name="Historian",
        provider="influxdb",
        status="connected",
        config={},
    )
    db.add(connection)
    db.flush()
    config = {
        "metrics": [
            {
                "connection_id": connection.id,
                "bucket_id": "operations",
                "measurement": "furnace",
                "field": "pressure",
                "label": "Top pressure",
                "tags": {},
            }
        ],
        "alert_rule": {
            "enabled": True,
            "severity": "critical" if escalate else "high",
            "publish": publish,
            "escalate": escalate,
            "consecutive_failures": consecutive,
            "assignee_id": engineer.id,
        },
    }
    job = JobService(db).create(
        engineer,
        workspace_id=workspace.id,
        name="Validate pressure",
        instructions="Validate readings",
        task_type="validation",
        config=config,
        interval_minutes=15,
        next_run_at=m.utcnow() + timedelta(minutes=15),
    )
    revision = GovernanceService(db).review(manager, job, approve=True)
    job.activation_not_before = m.utcnow() - timedelta(minutes=1)
    revision.reviewed_at = m.utcnow() - timedelta(hours=2)
    db.flush()
    return manager, engineer, db.get(m.User, job.execution_user_id), job, revision


def occurrence(db, job, when, outcome="fail"):
    data = {
        "task_type": "validation",
        "outcome": outcome,
        "metrics": [
            {
                "label": "Top pressure",
                "field": "pressure",
                "windows": {
                    "current": {"outcome": outcome, "reasons": ["Readings exceed configured limit"]}
                },
            }
        ],
    }
    run = m.JobRun(
        job_id=job.id,
        scheduled_for=when,
        status="succeeded",
        attempts=1,
        available_at=when,
        outcome=data,
        result="Validation result",
    )
    db.add(run)
    db.flush()
    report = m.Report(
        workspace_id=job.workspace_id,
        job_run_id=run.id,
        title="Validation",
        body="Evidence",
        result_data=data,
        source_refs=[],
    )
    db.add(report)
    db.flush()
    return run, data


def test_consecutive_failures_create_one_finding_and_repeated_runs_update_it(db):
    manager, engineer, principal, job, _ = setup(db)
    service = IncidentService(db)
    start = m.utcnow() - timedelta(minutes=45)
    first, data = occurrence(db, job, start)
    assert service.record_finding(principal, job, data, first.id) == []
    second, data = occurrence(db, job, start + timedelta(minutes=15))
    finding = service.record_finding(principal, job, data, second.id)[0]
    assert not finding.published and finding.assignee_id == engineer.id
    third, data = occurrence(db, job, start + timedelta(minutes=30))
    assert service.record_finding(principal, job, data, third.id)[0].id == finding.id
    assert service.record_finding(principal, job, data, third.id)[0].id == finding.id
    assert db.scalar(select(func.count()).select_from(m.Incident)) == 1
    assert db.scalar(select(func.count()).select_from(m.IncidentEvent)) == 2
    assert db.scalar(select(func.count()).select_from(m.Notification)) == 1
    service.transition(manager, finding.id, action="resolve", reason="Sensor fixed")
    assert service.record_finding(principal, job, data, third.id)[0].status == "resolved"
    fourth, data = occurrence(db, job, start + timedelta(minutes=45))
    new = service.record_finding(principal, job, data, fourth.id)[0]
    assert new.id != finding.id
    assert finding.status == "resolved"


def test_pass_window_breaks_consecutive_failure_gate(db):
    _, _, principal, job, _ = setup(db)
    service = IncidentService(db)
    start = m.utcnow() - timedelta(minutes=30)
    occurrence(db, job, start, "fail")
    occurrence(db, job, start + timedelta(minutes=15), "pass")
    third, data = occurrence(db, job, start + timedelta(minutes=30), "fail")
    assert service.record_finding(principal, job, data, third.id) == []


def test_manager_approved_publication_and_escalation_reaches_parent(db):
    manager, _, principal, job, _ = setup(db, publish=True, escalate=True, consecutive=1)
    run, data = occurrence(db, job, m.utcnow())
    finding = IncidentService(db).record_finding(principal, job, data, run.id)[0]
    assert finding.published and finding.escalated
    assert db.scalar(
        select(m.Notification).where(
            m.Notification.user_id == manager.id, m.Notification.resource_type == "publication"
        )
    )


def test_unapproved_config_change_cannot_publish(db):
    _, _, principal, job, _ = setup(db, consecutive=1)
    run, data = occurrence(db, job, m.utcnow())
    job.config = {**job.config, "alert_rule": {**job.config["alert_rule"], "publish": True}}
    db.flush()
    with pytest.raises(ServiceError, match="changed"):
        IncidentService(db).record_finding(principal, job, data, run.id)
    assert db.scalar(select(func.count()).select_from(m.Incident)) == 0


def test_validation_cannot_use_preapproval_failures(db):
    _, _, principal, job, revision = setup(db)
    now = m.utcnow()
    revision.reviewed_at = now - timedelta(minutes=5)
    occurrence(db, job, now - timedelta(minutes=15))
    run, data = occurrence(db, job, now)
    assert IncidentService(db).record_finding(principal, job, data, run.id) == []


def test_alert_escalation_requires_explicit_published_critical_rule():
    with pytest.raises(ServiceError):
        validate_alert_rule(
            {"enabled": True, "severity": "high", "publish": True, "escalate": True}
        )
