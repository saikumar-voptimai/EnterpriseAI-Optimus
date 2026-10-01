"""Natural language job setup uses LangGraph, preserves source IDs and stays a draft."""

import asyncio
import json

import pytest
from sqlalchemy import func, select

from app.agents.runtime import AgentRunner
from app.models import Membership, ScheduledJob, Scope, User, Workspace
from app.models_connections import Connection
from app.services.errors import ServiceError
from app.services.gateway import Completion
from app.services.job_drafts import JobDraftService


def seed(db):
    user = User(
        name="Engineer", email="drafts@test.local", password_hash="test", active=True, clearance=3
    )
    scope = Scope(name="Plant", kind="plant")
    db.add_all([user, scope])
    db.flush()
    workspace = Workspace(
        name="Utilities", scope_id=scope.id, created_by=user.id, external_ai_enabled=True
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="member"))
    connection = Connection(
        owner_id=user.id,
        workspace_id=workspace.id,
        name="Utility readings",
        provider="influxdb",
        status="connected",
        config={"url": "http://secret-internal-host:8086", "bucket_ids": ["permitted"]},
        encrypted_credentials="never-send-this-secret",
    )
    db.add(connection)
    db.commit()
    return user, workspace, connection


def proposed(index=None):
    return {
        "name": "Temperature validation",
        "instructions": "Validate every 15 minutes and alert after three bad windows above 90.",
        "task_type": "validation",
        "interval_minutes": 15,
        "window_minutes": 15,
        "metric_proposals": [
            {
                "metric_index": index,
                "label": "Temperature",
                "minimum": None,
                "maximum": 90.0,
                "max_age_minutes": None,
                "expected_interval_seconds": None,
            }
        ],
        "alert_rule": {
            "enabled": True,
            "severity": "high",
            "publish": False,
            "escalate": False,
            "consecutive_failures": 3,
        },
        "questions": [],
    }


class Gateway:
    def __init__(self, payload, before_return=None):
        self.payload, self.messages, self.before_return = payload, None, before_return

    async def complete(self, messages, **kwargs):
        self.messages = messages
        assert kwargs["response_format"]["json_schema"]["strict"]
        if self.before_return:
            self.before_return()
        return Completion(json.dumps(self.payload), "test/model-a", {}, "test-draft")

    async def complete_turn(self, *args, **kwargs):
        raise AssertionError("Drafting must never expose execution tools")


def test_language_draft_uses_shared_graph_without_creating_job_or_inventing_sources(db, db_factory):
    user, workspace, connection = seed(db)
    gateway = Gateway(proposed())
    service = JobDraftService(db, runner=AgentRunner(session_factory=db_factory, gateway=gateway))
    response = asyncio.run(
        service.draft(
            user,
            workspace_id=workspace.id,
            instructions="Validate temperature every 15 minutes. Alert after 3 bad windows above 90.",
            external_ai_consent=True,
        )
    )
    assert response["draft"]["config"]["metrics"] == []
    assert response["draft"]["config"]["alert_rule"]["consecutive_failures"] == 3
    assert response["metric_suggestions"][0]["maximum"] == 90
    assert response["questions"] and response["requires_manager_approval"]
    assert db.scalar(select(func.count()).select_from(ScheduledJob)) == 0
    prompt = json.dumps(gateway.messages)
    assert connection.id in prompt and "permitted" in prompt
    assert "secret-internal-host" not in prompt and "never-send-this-secret" not in prompt


def test_draft_applies_threshold_to_selected_signal_and_preserves_coordinates(db, db_factory):
    user, workspace, connection = seed(db)
    metric = {
        "connection_id": connection.id,
        "bucket_id": "permitted",
        "measurement": "pump",
        "field": "bearing_temperature",
        "unit": "C",
        "tags": {"unit": "P-04"},
    }
    service = JobDraftService(
        db, runner=AgentRunner(session_factory=db_factory, gateway=Gateway(proposed(0)))
    )
    response = asyncio.run(
        service.draft(
            user,
            workspace_id=workspace.id,
            selected_metrics=[metric],
            instructions="Alert after 3 readings above 90 degrees",
            external_ai_consent=True,
        )
    )
    selected = response["draft"]["config"]["metrics"][0]
    assert all(selected[key] == value for key, value in metric.items())
    assert selected["max"] == 90
    assert db.scalar(select(func.count()).select_from(ScheduledJob)) == 0


def test_draft_rejects_unavailable_signal_reference(db, db_factory):
    user, workspace, _ = seed(db)
    service = JobDraftService(
        db, runner=AgentRunner(session_factory=db_factory, gateway=Gateway(proposed(8)))
    )
    with pytest.raises(ServiceError, match="unavailable or repeated"):
        asyncio.run(
            service.draft(
                user,
                workspace_id=workspace.id,
                instructions="Validate the signal",
                external_ai_consent=True,
            )
        )


def test_draft_is_discarded_if_consumed_connection_changes_during_generation(db, db_factory):
    user, workspace, connection = seed(db)
    connection_id = connection.id

    def disconnect():
        with db_factory() as other:
            other.get(Connection, connection_id).status = "disconnected"
            other.commit()

    service = JobDraftService(
        db, runner=AgentRunner(session_factory=db_factory, gateway=Gateway(proposed(), disconnect))
    )
    with pytest.raises(ServiceError, match="changed|authorized"):
        asyncio.run(
            service.draft(
                user,
                workspace_id=workspace.id,
                instructions="Validate the signal",
                external_ai_consent=True,
            )
        )


def test_draft_requires_consent_before_any_model_call(db, db_factory):
    user, workspace, _ = seed(db)
    gateway = Gateway(proposed())
    service = JobDraftService(db, runner=AgentRunner(session_factory=db_factory, gateway=gateway))
    with pytest.raises(ServiceError, match="Allow"):
        asyncio.run(
            service.draft(user, workspace_id=workspace.id, instructions="Validate the signal")
        )
    assert gateway.messages is None
