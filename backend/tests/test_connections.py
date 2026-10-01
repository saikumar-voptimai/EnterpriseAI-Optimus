"""Connector protocol, authorization, bounded queries and durable dispatch checks."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from app.connectors.base import CredentialVault, ConnectorHTTP
from app.connectors.influxdb import InfluxAdapter
from app.connectors.microsoft import MicrosoftAdapter
from app.models import User, Scope, Workspace, Membership, utcnow
from app.models_connections import Connection, CalendarEvent, OAuthAttempt, Delivery
from app.repositories.access import NotFound
from app.services.connections import ConnectionService
from app.services.calendar_sync import CalendarSyncService
from app.services.delivery import DeliveryService
from app.services.errors import ServiceError, ProviderError


def settings():
    return SimpleNamespace(
        credential_encryption_key=Fernet.generate_key().decode(),
        microsoft_client_id="client-id",
        microsoft_client_secret="client-secret",
        microsoft_tenant_id="organizations",
        app_origin="https://app.example.test",
        connector_timeout_seconds=30,
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_from="assistant@example.test",
        smtp_starttls=True,
        smtp_username="",
        smtp_password="",
    )


def people(db):
    first = User(
        email="first@connector.test",
        name="First",
        password_hash="unused",
        active=True,
        is_admin=True,
        clearance=3,
    )
    second = User(
        email="second@connector.test",
        name="Second",
        password_hash="unused",
        active=True,
        clearance=3,
    )
    db.add_all([first, second])
    db.flush()
    return first, second


def test_credentials_encrypted_and_invalid_key_fails():
    vault = CredentialVault(Fernet.generate_key().decode())
    encrypted = vault.encrypt({"secret": "never expose me"})
    assert "never expose me" not in encrypted
    assert vault.decrypt(encrypted) == {"secret": "never expose me"}
    with pytest.raises(ServiceError):
        CredentialVault(Fernet.generate_key().decode()).decrypt(encrypted)


def test_pkce_authorization_has_exact_callback_and_separate_transcript_permissions():
    adapter = MicrosoftAdapter(settings())
    base = parse_qs(urlsplit(adapter.authorize_url("state", "challenge")).query)
    enhanced = parse_qs(urlsplit(adapter.authorize_url("state", "challenge", True)).query)
    assert base["redirect_uri"] == ["https://app.example.test/api/connections/microsoft/callback"]
    assert base["code_challenge_method"] == ["S256"]
    assert "OnlineMeetingTranscript" not in base["scope"][0]
    assert "OnlineMeetingTranscript.Read.All" in enhanced["scope"][0]


def test_microsoft_does_not_follow_untrusted_pagination_url():
    def responder(request):
        return httpx.Response(
            200, json={"value": [], "@odata.nextLink": "https://evil.example/token"}
        )

    adapter = MicrosoftAdapter(settings(), ConnectorHTTP(httpx.MockTransport(responder)))
    with pytest.raises(ProviderError, match="continuation"):
        asyncio.run(adapter.calendars("secret-token"))


def test_influx_query_is_generated_bounded_and_rejects_interpolation():
    requests = []

    def responder(request):
        requests.append(request)
        return httpx.Response(
            200, content=",result,table,_time,_value\n,,0,2026-01-01T00:30:00Z,42.5\n"
        )

    adapter = InfluxAdapter(
        {
            "url": "http://127.0.0.1:8086",
            "allow_private_network": True,
            "org_id": "org",
            "bucket_ids": ["b"],
        },
        {"token": "readonly"},
        ConnectorHTTP(httpx.MockTransport(responder)),
    )
    start = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
    result = asyncio.run(
        adapter.query_series(
            bucket_id="b",
            measurement="furnace",
            field="temperature",
            start=start,
            stop=start + timedelta(hours=1),
            aggregate_minutes=60,
            tags={"unit": "BF1"},
        )
    )
    assert result == [{"timestamp": "2026-01-01T00:30:00+00:00", "value": 42.5}]
    flux = json.loads(requests[0].content)["query"]
    assert "offset: 1800s" in flux and "limit(n: 1001)" in flux and 'r["unit"] == "BF1"' in flux
    assert requests[0].url.path == "/api/v2/query"
    with pytest.raises(ServiceError, match="interpolation"):
        asyncio.run(
            adapter.query_series(
                bucket_id="b",
                measurement='${die(msg:"bad")}',
                field="x",
                start=start,
                stop=start + timedelta(minutes=1),
            )
        )
    assert len(requests) == 1


def test_influx_overflow_never_silently_truncates():
    body = ",result,table,_time,_value\n,,0,2026-01-01T00:00:00Z,1\n,,0,2026-01-01T00:01:00Z,2\n"
    adapter = InfluxAdapter(
        {
            "url": "http://127.0.0.1:8086",
            "allow_private_network": True,
            "org_id": "org",
            "bucket_ids": ["b"],
        },
        {"token": "readonly"},
        ConnectorHTTP(httpx.MockTransport(lambda r: httpx.Response(200, content=body))),
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ProviderError, match="row limit"):
        asyncio.run(
            adapter.query_series(
                bucket_id="b",
                measurement="x",
                field="x",
                start=start,
                stop=start + timedelta(hours=1),
                limit=1,
            )
        )


def test_oauth_state_one_use_and_user_bound_without_session_cookie(db):
    user, other = people(db)
    cfg = settings()
    service = ConnectionService(db, cfg)
    begun = service.start_microsoft(user)
    state = parse_qs(urlsplit(begun["authorization_url"]).query)["state"][0]
    db.commit()
    attempt = db.scalar(select(OAuthAttempt))
    assert state not in attempt.state_hash and attempt.connection_id == begun["connection_id"]
    obj = asyncio.run(service.finish_microsoft(state=state, error="access_denied"))
    assert obj.owner_id == user.id and obj.status == "error"
    with pytest.raises(ServiceError, match="already used"):
        asyncio.run(service.finish_microsoft(state=state, error="access_denied"))
    with pytest.raises(NotFound):
        service.repo.get(other, obj.id)


def test_outlook_sync_removes_cancelled_missing_occurrences_and_is_private(db):
    user, other = people(db)
    cfg = settings()
    vault = CredentialVault(cfg.credential_encryption_key)
    conn = Connection(
        owner_id=user.id,
        name="Calendar",
        provider="microsoft",
        status="connected",
        config={"calendar_ids": ["default"]},
        encrypted_credentials=vault.encrypt(
            {"access_token": "token", "expires_at": (utcnow() + timedelta(hours=1)).isoformat()}
        ),
    )
    db.add(conn)
    db.flush()
    old = CalendarEvent(
        connection_id=conn.id,
        owner_id=user.id,
        calendar_id="default",
        provider_id="removed",
        title="Removed",
        starts_at=utcnow(),
        ends_at=utcnow() + timedelta(hours=1),
        cancelled=False,
        busy=True,
        details={},
    )
    db.add(old)
    db.commit()
    now = utcnow()
    event = {
        "id": "new",
        "subject": "Meeting",
        "start": {"dateTime": now.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": (now + timedelta(hours=1)).isoformat(), "timeZone": "UTC"},
        "isCancelled": False,
        "showAs": "busy",
    }
    http = ConnectorHTTP(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"value": [event]}))
    )
    sync = CalendarSyncService(db, cfg, http)
    asyncio.run(sync.sync(user, conn.id))
    db.commit()
    rows = sync.events(user, now - timedelta(hours=1), now + timedelta(hours=2))
    assert len(rows) == 1 and rows[0]["title"] == "Meeting"
    assert sync.events(other, now - timedelta(hours=1), now + timedelta(hours=2)) == []
    service = ConnectionService(db, cfg)
    service.disconnect(user, conn.id)
    db.commit()
    assert sync.events(user, now - timedelta(hours=1), now + timedelta(hours=2)) == []


def test_delivery_undo_and_idempotency_survive_commit(db, monkeypatch):
    user, _ = people(db)
    cfg = settings()
    service = DeliveryService(db, cfg)
    args = dict(
        channel="email",
        recipient=user.email,
        subject="Brief",
        body="Approved brief",
        request_id="delivery-request-1",
    )
    message = service.enqueue(user, **args)
    db.commit()
    assert service.enqueue(user, **args).id == message.id
    with pytest.raises(ServiceError, match="different delivery"):
        service.enqueue(user, **{**args, "body": "Changed"})
    calls = []
    monkeypatch.setattr(service, "_send_email", lambda row: calls.append(row.id))
    assert asyncio.run(service.tick()) is False
    service.cancel(user, message.id)
    db.commit()
    message.available_at = utcnow() - timedelta(seconds=1)
    message.undo_until = utcnow() - timedelta(seconds=1)
    db.commit()
    asyncio.run(service.tick())
    assert calls == [] and message.status == "cancelled"


def test_crashed_delivery_is_unknown_not_sent_twice(db, monkeypatch):
    user, _ = people(db)
    service = DeliveryService(db, settings())
    row = service.enqueue(
        user,
        channel="email",
        recipient=user.email,
        subject="Brief",
        body="Body",
        request_id="crash-request-1",
    )
    row.status = "sending"
    row.updated_at = utcnow() - timedelta(minutes=10)
    db.commit()
    calls = []
    monkeypatch.setattr(service, "_send_email", lambda item: calls.append(item.id))
    asyncio.run(service.tick())
    assert row.status == "unknown" and calls == []


def test_free_slots_never_ignore_an_errored_calendar(db):
    user, _ = people(db)
    now = utcnow()
    cfg = settings()
    db.add_all(
        [
            Connection(
                owner_id=user.id,
                name="Primary",
                provider="microsoft",
                status="connected",
                config={"calendar_ids": ["main"]},
                last_synced_at=now,
            ),
            Connection(
                owner_id=user.id,
                name="Secondary",
                provider="microsoft",
                status="error",
                config={"calendar_ids": ["other"]},
                last_synced_at=now - timedelta(minutes=30),
            ),
        ]
    )
    db.commit()
    result = CalendarSyncService(db, cfg).free_slots(
        user, now + timedelta(hours=1), now + timedelta(hours=2)
    )
    assert result == {"source": "unavailable", "slots": []}


def test_delivery_rechecks_source_permission_before_dispatch(db, monkeypatch):
    from app.models import Note

    user, _ = people(db)
    note = Note(owner_id=user.id, title="Private", body="Body", kind="thought")
    db.add(note)
    db.flush()
    service = DeliveryService(db, settings())
    message = service.enqueue(
        user,
        channel="email",
        recipient=user.email,
        subject="Brief",
        body="Old content",
        request_id="revoke-delivery-1",
        source_refs=[{"type": "note", "id": note.id}],
    )
    db.delete(note)
    message.available_at = utcnow() - timedelta(seconds=1)
    message.undo_until = utcnow() - timedelta(seconds=1)
    db.commit()
    calls = []
    monkeypatch.setattr(service, "_send_email", lambda row: calls.append(row.id))
    asyncio.run(service.tick())
    assert message.status == "failed" and calls == []
