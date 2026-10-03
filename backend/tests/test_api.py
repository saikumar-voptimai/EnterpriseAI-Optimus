"""Real HTTP/auth/persistence checks against the migrated PostgreSQL schema."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, func, delete
from app import models as m
from app.auth import hash_password
from app.services.gateway import ModelTurn
from app.services.errors import ProviderError

PASSWORD = "test-passphrase-37!"


def seed(db, admin=True):
    u = m.User(
        name="Test operator",
        email="operator@example.test",
        password_hash=hash_password(PASSWORD),
        is_admin=admin,
        active=True,
        clearance=3,
    )
    other = m.User(
        name="Other user",
        email="other@example.test",
        password_hash=hash_password(PASSWORD),
        is_admin=False,
        active=True,
        clearance=2,
    )
    scope = m.Scope(name="Operations", kind="division")
    db.add_all([u, other, scope])
    db.flush()
    workspace = m.Workspace(
        name="Shared workspace",
        description="",
        scope_id=scope.id,
        classification=2,
        external_ai_enabled=True,
        created_by=u.id,
    )
    db.add(workspace)
    db.flush()
    db.add(m.Membership(workspace_id=workspace.id, user_id=u.id, role="manager"))
    db.commit()
    return u, other, scope, workspace


def login(client, email="operator@example.test"):
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


def test_auth_csrf_and_private_persistence(client, db):
    u, other, _, workspace = seed(db)
    assert client.get("/api/notes").status_code == 401
    auth = login(client)
    assert "password_hash" not in auth["user"]
    response = client.post(
        "/api/notes", json={"body": "Remember the vibration inspection", "kind": "observation"}
    )
    assert response.status_code == 201, response.text
    note_id = response.json()["id"]
    assert client.get("/api/notes").json()[0]["id"] == note_id
    assert (
        client.post(
            "/api/notes", json={"body": "bad origin"}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/notes", json={"body": "no CSRF"}, headers={"X-CSRF-Token": ""}
        ).status_code
        == 403
    )
    assert client.post("/api/auth/logout").status_code == 200
    login(client, other.email)
    assert client.get("/api/notes").json() == []
    assert client.patch(f"/api/notes/{note_id}", json={"body": "overwrite"}).status_code == 404
    assert client.get("/api/documents", params={"workspace_id": workspace.id}).status_code == 404
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/notes/not-a-uuid").status_code in (404, 422)


def test_documents_and_chat_actual_gateway_contract(client, db, monkeypatch):
    u, _, _, workspace = seed(db)
    login(client)
    private = client.post("/api/notes", json={"body": "PRIVATE_DIARY_DO_NOT_SHARE"}).json()
    upload = client.post(
        "/api/documents",
        data={"workspace_id": workspace.id},
        files={
            "file": ("sop.txt", b"Inspection SOP: record the bearing temperature.", "text/plain")
        },
    )
    assert upload.status_code == 201, upload.text
    assert "body" not in upload.json()
    doc_id = upload.json()["id"]
    assert client.get(f"/api/documents/{doc_id}").json()["body"].startswith("Inspection SOP")
    conv = client.post(
        "/api/conversations", json={"title": "Inspection", "workspace_id": workspace.id}
    ).json()
    called = []

    async def complete(self, messages, model=None, request_id=None, **kwargs):
        called.append(messages)
        assert "PRIVATE_DIARY_DO_NOT_SHARE" not in str(messages)
        assert "bearing temperature" in str(messages)
        return ModelTurn(
            "Record the temperature [sop.txt].", model, {"total_tokens": 30}, "request-test", []
        )

    monkeypatch.setattr("app.main.OpenRouterGateway.complete_turn", complete)
    endpoint = f"/api/conversations/{conv['id']}/messages"
    assert client.post(endpoint, json={"content": "What should I record?"}).status_code == 403
    assert (
        client.post(
            endpoint,
            json={
                "content": "What should I record?",
                "external_ai_consent": True,
                "model": "unapproved/model",
            },
        ).status_code
        == 400
    )
    result = client.post(
        endpoint,
        json={
            "content": "What should I record?",
            "external_ai_consent": True,
            "model": "test/model-a",
        },
    )
    assert result.status_code == 200, result.text
    answer = result.json()["assistant_message"]
    # Clients see the model level, never the provider model name.
    assert answer["model"] == "medium" and "test/model-a" not in result.text
    assert any(source["id"] == doc_id for source in answer["source_refs"])
    assert len(client.get(endpoint).json()) == 2
    assert len(called) == 1

    async def fail(*args, **kwargs):
        raise ProviderError("Provider temporarily unavailable", retryable=True)

    monkeypatch.setattr("app.main.OpenRouterGateway.complete_turn", fail)
    retry = client.post(
        endpoint, json={"content": "Try another question", "external_ai_consent": True}
    )
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued"
    assert client.get("/api/agent-runs/" + retry.json()["id"]).json()["attempts"] == 1
    # Durable runs retain the submitted user message when a provider attempt fails.
    assert len(client.get(endpoint).json()) == 3


def test_permission_revoked_during_chat_discards_response(client, db, db_factory, monkeypatch):
    u, other, _, workspace = seed(db)
    db.add(m.Membership(workspace_id=workspace.id, user_id=other.id, role="member"))
    db.commit()
    login(client, other.email)
    conv = client.post(
        "/api/conversations", json={"title": "Test", "workspace_id": workspace.id}
    ).json()

    async def revoke(self, messages, model=None, request_id=None, **kwargs):
        with db_factory() as transaction:
            transaction.execute(
                delete(m.Membership).where(
                    m.Membership.workspace_id == workspace.id, m.Membership.user_id == other.id
                )
            )
            transaction.commit()
        return ModelTurn("Must be discarded", model, {}, "request-revoked", [])

    monkeypatch.setattr("app.main.OpenRouterGateway.complete_turn", revoke)
    result = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Explain", "external_ai_consent": True},
    )
    assert result.status_code == 502, result.text
    assert (
        db.scalar(select(func.count()).select_from(m.Message).where(m.Message.role == "assistant"))
        == 0
    )
    assert (
        db.scalar(select(func.count()).select_from(m.Message).where(m.Message.role == "user")) == 1
    )


def test_private_admin_isolation_and_forgetting(client, db):
    u, other, _, _ = seed(db)
    login(client, other.email)
    note = client.post("/api/notes", json={"body": "A personal hypothesis"}).json()
    memory = client.post(
        "/api/memories", json={"note_id": note["id"], "text": "An explicit memory"}
    ).json()
    assert client.delete(f"/api/memories/{memory['id']}").status_code == 200
    assert client.get("/api/memories").json() == []
    assert client.get("/api/notes").json()[0]["body"] == "A personal hypothesis"
    client.post("/api/auth/logout")
    login(client)
    assert client.patch(f"/api/notes/{note['id']}", json={"body": "Admin edit"}).status_code == 404
    assert (
        client.post(
            "/api/memories", json={"note_id": note["id"], "text": "Admin retained"}
        ).status_code
        == 404
    )


def test_upload_budget_and_unknown_api_do_not_serve_spa(client, db):
    seed(db)
    login(client)
    response = client.post(
        "/api/documents", files={"file": ("oversize.txt", b"x" * (2097152 + 65537), "text/plain")}
    )
    assert response.status_code == 413
    streamed = client.post(
        "/api/notes",
        content=iter([b"x" * (2097152 + 65537)]),
        headers={"Content-Type": "application/json"},
    )
    assert streamed.status_code == 413
    assert client.get("/api/not-a-real-resource").status_code == 404
    assert client.get("/api/health/ready").json() == {"status": "ready"}
    assert client.get("/api/bootstrap").json()["workspaces"][0]["membership_role"] == "manager"
