"""Incident privacy, permission revocation, and scheduled-work HTTP checks."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app import models as m
from app.auth import hash_password

PASSWORD = "operations-test-password-41!"


def seed_operations(db):
    manager = m.User(
        name="Manager",
        email="manager@ops.test",
        password_hash=hash_password(PASSWORD),
        is_admin=True,
        active=True,
        clearance=3,
    )
    member = m.User(
        name="Member",
        email="member@ops.test",
        password_hash=hash_password(PASSWORD),
        active=True,
        clearance=2,
    )
    reviewer = m.User(
        name="Reviewer",
        email="reviewer@ops.test",
        password_hash=hash_password(PASSWORD),
        active=True,
        clearance=2,
    )
    scope = m.Scope(name="Operations", kind="division")
    db.add_all([manager, member, reviewer, scope])
    db.flush()
    workspace = m.Workspace(
        name="Maintenance",
        scope_id=scope.id,
        created_by=manager.id,
        classification=2,
        external_ai_enabled=True,
    )
    db.add(workspace)
    db.flush()
    db.add_all(
        [
            m.Membership(workspace_id=workspace.id, user_id=manager.id, role="manager"),
            m.Membership(workspace_id=workspace.id, user_id=member.id, role="member"),
        ]
    )
    db.commit()
    return manager, member, reviewer, scope, workspace


def sign_in(client, user):
    response = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def incident_payload(workspace, assignee, **changes):
    return {
        **dict(
            workspace_id=workspace.id,
            title="Bearing inspection",
            summary="Share this excerpt.",
            severity="critical",
            assignee_id=assignee.id,
            decision="Schedule an inspection.",
            published=False,
            request_id=str(uuid4()),
        ),
        **changes,
    }


def test_incident_excerpt_idempotency_and_read_is_not_acknowledgement(client, db):
    manager, member, _, _, workspace = seed_operations(db)
    sign_in(client, member)
    note = client.post(
        "/api/notes", json={"body": "PRIVATE PREAMBLE. Share this excerpt. PRIVATE CONCLUSION."}
    ).json()
    payload = incident_payload(workspace, member, note_id=note["id"])
    invalid = client.post("/api/incidents", json={**payload, "summary": "An altered excerpt."})
    assert invalid.status_code == 422, invalid.text
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201, response.text
    incident = response.json()
    assert note["id"] not in str(incident) and "PRIVATE" not in str(incident)
    repeated = client.post("/api/incidents", json=payload)
    assert repeated.status_code == 201 and repeated.json()["id"] == incident["id"]
    assert (
        client.post("/api/incidents", json={**payload, "decision": "Another decision"}).status_code
        == 409
    )
    notifications = client.get("/api/notifications").json()
    assert len(notifications) == 1
    assert client.post(f"/api/notifications/{notifications[0]['id']}/read").status_code == 200
    assert client.get("/api/incidents").json()[0]["status"] == "open"
    actions = f"/api/incidents/{incident['id']}/actions"
    assert client.post(actions, json={"action": "publish"}).status_code == 403
    assert client.post(actions, json={"action": "acknowledge"}).json()["status"] == "acknowledged"
    assert client.post(actions, json={"action": "resolve"}).status_code == 422
    assert (
        client.post(actions, json={"action": "resolve", "reason": "Bearing replaced."}).json()[
            "status"
        ]
        == "resolved"
    )
    sign_in(client, manager)
    audit = client.get("/api/admin/audit").json()
    assert note["id"] not in str(audit) and "PRIVATE" not in str(audit)


def test_summary_notification_is_reauthorized_after_grant_revocation(client, db):
    manager, _, reviewer, scope, workspace = seed_operations(db)
    sign_in(client, manager)
    grant = client.post(
        "/api/admin/scope-grants", json={"user_id": reviewer.id, "scope_id": scope.id}
    )
    assert grant.status_code == 200, grant.text
    assert client.get("/api/admin/scope-grants").json()[0]["can_review"] is True
    created = client.post(
        "/api/incidents", json=incident_payload(workspace, manager, published=True)
    )
    assert created.status_code == 201, created.text
    incident = created.json()
    sign_in(client, reviewer)
    assert client.get("/api/incidents").json() == []
    summaries = client.get("/api/publications").json()
    assert (
        len(summaries) == 1
        and summaries[0]["id"] == incident["id"]
        and "request_id" not in summaries[0]
    )
    assert client.put("/api/subscriptions", json={"scope_ids": [scope.id]}).status_code == 200
    notification = client.get("/api/notifications").json()[0]
    assert notification["resource_type"] == "publication"
    sign_in(client, manager)
    assert client.delete(f"/api/admin/scope-grants/{reviewer.id}/{scope.id}").status_code == 200
    sign_in(client, reviewer)
    assert client.get("/api/publications").json() == []
    assert client.get("/api/notifications").json() == []
    assert client.get("/api/subscriptions").json() == []
    assert client.post(f"/api/notifications/{notification['id']}/read").status_code == 404
    assert client.put("/api/subscriptions", json={"scope_ids": [scope.id]}).status_code == 403


def test_escalation_requires_published_critical_and_admin_guard(client, db):
    manager, _, _, _, workspace = seed_operations(db)
    sign_in(client, manager)
    created = client.post(
        "/api/incidents", json=incident_payload(workspace, manager, severity="high")
    ).json()
    actions = f"/api/incidents/{created['id']}/actions"
    assert client.post(actions, json={"action": "escalate"}).status_code == 409
    assert client.post(actions, json={"action": "publish"}).status_code == 200
    assert client.post(actions, json={"action": "escalate"}).status_code == 409
    critical = client.post(
        "/api/incidents", json=incident_payload(workspace, manager, published=True)
    ).json()
    critical_actions = f"/api/incidents/{critical['id']}/actions"
    escalation = client.post(critical_actions, json={"action": "escalate"})
    assert escalation.status_code == 200 and escalation.json()["escalated"] is True
    assert client.post(critical_actions, json={"action": "escalate"}).status_code == 200
    assert client.patch(f"/api/admin/users/{manager.id}", json={"active": False}).status_code == 409
    assert client.get("/api/auth/me").status_code == 200


def test_legacy_job_config_and_execution_remain_owner_only(client, db):
    manager, member, _, _, workspace = seed_operations(db)
    sign_in(client, manager)
    response = client.post(
        "/api/jobs",
        json={
            "name": "Maintenance summary",
            "instructions": "Summarize shared maintenance sources.",
            "model": "test/model-a",
            "workspace_id": workspace.id,
            "next_run_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "interval_minutes": 60,
            "external_ai_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    job = response.json()
    # Simulate a pre-upgrade schedule retaining the explicitly migrated legacy policy.
    from app.models import ScheduledJob

    persisted = db.get(ScheduledJob, job["id"])
    persisted.approval_status, persisted.status, persisted.execution_user_id = (
        "legacy",
        "active",
        None,
    )
    db.commit()
    sign_in(client, member)
    assert client.get("/api/jobs").json()[0]["id"] == job["id"]
    assert client.patch(f"/api/jobs/{job['id']}", json={"status": "paused"}).status_code == 403
    assert client.post(f"/api/jobs/{job['id']}/run").status_code == 403
    sign_in(client, manager)
    run = client.post(f"/api/jobs/{job['id']}/run")
    assert run.status_code == 202, run.text
    assert run.json()["status"] == "queued" and "lease_token" not in run.json()
    assert len(client.get(f"/api/jobs/{job['id']}/runs").json()) == 1
