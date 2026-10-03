"""Google Workspace, Slack and implicit-TLS email integrations with mocked providers."""

import asyncio
import json
import smtplib
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from app.connectors.base import ConnectorHTTP, CredentialVault
from app.connectors.google import GoogleAdapter
from app.models import Document, utcnow
from app.models_connections import Connection
from app.repositories import NotFound
from app.services.calendar_sync import CalendarSyncService
from app.services.connections import ConnectionService
from app.services.delivery import DeliveryService
from app.services.errors import ProviderError, ServiceError
from test_connections import people, settings


def google_settings(**overrides):
    return SimpleNamespace(
        **{
            **vars(settings()),
            "google_client_id": "google-client",
            "google_client_secret": "google-secret",
            "max_upload_bytes": 2_000_000,
            "allow_external_ai": True,
            "openrouter_api_key": "",
            "embeddings_enabled": False,
            **overrides,
        }
    )


def google_connection(db, user, cfg, expires_in_hours=1):
    conn = Connection(
        owner_id=user.id,
        name="Google",
        provider="google",
        status="connected",
        config={"calendar_ids": ["primary"]},
        encrypted_credentials=CredentialVault(cfg.credential_encryption_key).encrypt(
            {
                "access_token": "google-token",
                "refresh_token": "refresh-1",
                "expires_at": (utcnow() + timedelta(hours=expires_in_hours)).isoformat(),
            }
        ),
    )
    db.add(conn)
    db.flush()
    return conn


def mock_http(responder):
    return ConnectorHTTP(httpx.MockTransport(responder))


def test_google_authorization_is_read_only_pkce_with_exact_callback():
    url = GoogleAdapter(google_settings()).authorize_url("state", "challenge")
    query = parse_qs(urlsplit(url).query)
    assert query["redirect_uri"] == ["https://app.example.test/api/connections/google/callback"]
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    api_scopes = [s for s in query["scope"][0].split() if s.startswith("https://")]
    assert api_scopes and all(s.endswith("readonly") for s in api_scopes)


def test_google_requires_installation_credentials():
    with pytest.raises(ServiceError, match="not configured"):
        GoogleAdapter(google_settings(google_client_id="")).authorize_url("s", "c")


def test_google_never_calls_hosts_outside_its_api_allowlist():
    adapter = GoogleAdapter(google_settings(), mock_http(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError, match="invalid endpoint"):
        asyncio.run(adapter.get("https://evil.example/calendar", "token"))


def test_google_calendar_sync_maps_events_including_all_day_and_free_time(db):
    user, other = people(db)
    cfg = google_settings()
    conn = google_connection(db, user, cfg)
    db.commit()
    now = utcnow().replace(microsecond=0)
    events = [
        {
            "id": "g1",
            "summary": "BF2 review",
            "start": {"dateTime": now.isoformat()},
            "end": {"dateTime": (now + timedelta(hours=1)).isoformat()},
            "hangoutLink": "https://meet.google.com/abc-mnop-xyz",
            "conferenceData": {"conferenceId": "abc-mnop-xyz"},
        },
        {
            "id": "g2",
            "summary": "Plant holiday",
            "transparency": "transparent",
            "start": {"date": now.date().isoformat()},
            "end": {"date": (now + timedelta(days=1)).date().isoformat()},
        },
    ]
    paths = []

    def responder(request):
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer google-token"
        return httpx.Response(200, json={"items": events})

    sync = CalendarSyncService(db, cfg, mock_http(responder))
    asyncio.run(sync.sync(user, conn.id))
    db.commit()
    window = (now - timedelta(days=1), now + timedelta(days=2))
    rows = {r["title"]: r for r in sync.events(user, *window)}
    assert paths == ["/calendar/v3/calendars/primary/events"]
    assert rows["BF2 review"]["busy"] is True
    assert rows["BF2 review"]["details"]["online_meeting"]["meeting_code"] == "abc-mnop-xyz"
    assert rows["Plant holiday"]["busy"] is False
    assert sync.events(other, *window) == []


def test_google_refresh_keeps_the_stored_refresh_token(db):
    user, _ = people(db)
    cfg = google_settings()
    conn = google_connection(db, user, cfg, expires_in_hours=-1)

    def responder(request):
        assert request.url.host == "oauth2.googleapis.com"
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

    service = ConnectionService(db, cfg, mock_http(responder))
    assert asyncio.run(service.google_token(conn)) == "fresh"
    stored = CredentialVault(cfg.credential_encryption_key).decrypt(conn.encrypted_credentials)
    assert stored["access_token"] == "fresh" and stored["refresh_token"] == "refresh-1"


def test_google_drive_import_creates_a_private_document(db):
    user, other = people(db)
    cfg = google_settings()
    conn = google_connection(db, user, cfg)

    def responder(request):
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"Tuyere cooling SOP: reduce blast by 5%.")
        return httpx.Response(
            200, json={"id": "f1", "name": "tuyere-sop.txt", "mimeType": "text/plain", "size": "40"}
        )

    service = ConnectionService(db, cfg, mock_http(responder))
    document = asyncio.run(service.import_drive_file(user, conn.id, "f1"))
    db.commit()
    stored = db.get(Document, document.id)
    assert stored.owner_id == user.id and stored.workspace_id is None
    assert "reduce blast" in stored.body and stored.filename == "tuyere-sop.txt"
    with pytest.raises(NotFound):
        asyncio.run(service.import_drive_file(other, conn.id, "f1"))


def test_google_meet_transcripts_are_found_by_meeting_code_and_exported_as_text():
    def responder(request):
        path = request.url.path
        if path == "/v2/conferenceRecords":
            assert request.url.params["filter"] == 'space.meeting_code = "abc-mnop-xyz"'
            return httpx.Response(
                200, json={"conferenceRecords": [{"name": "conferenceRecords/c1"}]}
            )
        if path == "/v2/conferenceRecords/c1/transcripts":
            return httpx.Response(
                200,
                json={
                    "transcripts": [
                        {
                            "name": "conferenceRecords/c1/transcripts/t1",
                            "state": "FILE_GENERATED",
                            "docsDestination": {"document": "doc1"},
                        },
                        {"name": "conferenceRecords/c1/transcripts/t2", "state": "STARTED"},
                    ]
                },
            )
        if path == "/v2/conferenceRecords/c1/transcripts/t1":
            return httpx.Response(200, json={"docsDestination": {"document": "doc1"}})
        assert path == "/drive/v3/files/doc1/export"
        return httpx.Response(200, content=b"Asha: Reduce blast volume.")

    adapter = GoogleAdapter(google_settings(), mock_http(responder))
    found = asyncio.run(adapter.meet_transcripts("token", "abc-mnop-xyz"))
    assert [t["id"] for t in found] == ["conferenceRecords/c1/transcripts/t1"]
    text = asyncio.run(adapter.meet_transcript_text("token", found[0]["id"]))
    assert text == "Asha: Reduce blast volume."
    with pytest.raises(ServiceError, match="Meet code"):
        asyncio.run(adapter.meet_transcripts("token", 'x" OR "1'))


def test_slack_delivery_only_targets_slack_webhooks_and_posts_the_message(db):
    user, _ = people(db)
    cfg = settings()
    service = ConnectionService(db, cfg)
    with pytest.raises(ServiceError):
        service.create(
            user,
            provider="slack_webhook",
            name="Bad",
            config={},
            credentials={"webhook_url": "https://evil.example/services/x"},
        )
    conn = service.create(
        user,
        provider="slack_webhook",
        name="Plant alerts",
        config={},
        credentials={"webhook_url": "https://hooks.slack.com/services/T1/B2/secret"},
    )
    posted = []

    def responder(request):
        posted.append((request.url.host, json.loads(request.content)))
        return httpx.Response(200, content=b"ok")

    delivery = DeliveryService(db, cfg, mock_http(responder))
    row = delivery.enqueue(
        user,
        channel="slack",
        recipient="",
        connection_id=conn.id,
        subject="BF2 alert",
        body="Hearth flow low",
        request_id="slack-request-1",
    )
    row.available_at = row.undo_until = utcnow() - timedelta(seconds=1)
    db.commit()
    asyncio.run(delivery.tick())
    assert row.status == "sent" and posted[0][0] == "hooks.slack.com"
    assert posted[0][1]["text"] == "BF2 alert"
    assert "Hearth flow low" in json.dumps(posted[0][1]["blocks"])


def test_port_465_email_uses_implicit_tls(db, monkeypatch):
    user, _ = people(db)
    cfg = SimpleNamespace(
        **{
            **vars(settings()),
            "smtp_port": 465,
            "smtp_username": "me@example.test",
            "smtp_password": "pw",
        }
    )
    used = []

    class FakeSSL:
        def __init__(self, host, port, timeout, context):
            used.append(("ssl", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            used.append("starttls")

        def login(self, username, password):
            used.append(("login", username))

        def send_message(self, message):
            used.append(("sent", message["To"]))

    def plain_smtp(*args, **kwargs):
        raise AssertionError("plain SMTP used on port 465")

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    monkeypatch.setattr(smtplib, "SMTP", plain_smtp)
    service = DeliveryService(db, cfg)
    row = service.enqueue(
        user,
        channel="email",
        recipient=user.email,
        subject="Brief",
        body="Body",
        request_id="ssl-request-1",
    )
    service._send_email(row)
    assert used[0] == ("ssl", "smtp.example.test", 465) and "starttls" not in used
    assert ("login", "me@example.test") in used and ("sent", user.email) in used
