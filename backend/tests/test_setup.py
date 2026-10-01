"""First-run account creation must be origin-bound, token-gated, and single-use."""

from sqlalchemy import func, select
from app import models as m
from app.config import get_settings

SETUP_TOKEN = "isolated-test-bootstrap-token-001"
PAYLOAD = {
    "token": SETUP_TOKEN,
    "email": "first-admin@example.test",
    "name": "Pilot administrator",
    "password": "setup-test-passphrase-37!",
    "enterprise_name": "Pilot enterprise",
}


def test_setup_creates_usable_admin_and_enterprise_once(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "bootstrap_token", SETUP_TOKEN)
    assert client.get("/api/setup/status").json()["required"] is True
    response = client.post("/api/setup", json=PAYLOAD)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["user"]["is_admin"] is True
    assert "password_hash" not in result["user"]
    assert result["csrf_token"]
    client.headers["X-CSRF-Token"] = result["csrf_token"]
    assert client.get("/api/bootstrap").status_code == 200
    assert client.get("/api/setup/status").json()["required"] is False
    assert db.scalar(select(func.count()).select_from(m.User)) == 1
    scope = db.scalar(select(m.Scope))
    assert scope.name == "Pilot enterprise"
    workspace = db.scalar(select(m.Workspace))
    assert workspace.scope_id == scope.id
    membership = db.scalar(select(m.Membership))
    assert membership.workspace_id == workspace.id and membership.role == "manager"
    db.rollback()
    assert (
        client.post("/api/setup", json={**PAYLOAD, "email": "another@example.test"}).status_code
        == 409
    )
    assert db.scalar(select(func.count()).select_from(m.User)) == 1


def test_setup_rejects_missing_or_incorrect_server_token(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "bootstrap_token", "")
    assert client.post("/api/setup", json=PAYLOAD).status_code == 403
    monkeypatch.setattr(get_settings(), "bootstrap_token", "a-different-long-bootstrap-token")
    assert client.post("/api/setup", json=PAYLOAD).status_code == 403
    assert db.scalar(select(func.count()).select_from(m.User)) == 0
    db.rollback()
    assert client.get("/api/setup/status").json()["required"] is True


def test_setup_rejects_other_origin_and_weak_password(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "bootstrap_token", SETUP_TOKEN)
    assert (
        client.post(
            "/api/setup", json=PAYLOAD, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert client.post("/api/setup", json={**PAYLOAD, "password": "short"}).status_code == 422
    assert db.scalar(select(func.count()).select_from(m.User)) == 0
