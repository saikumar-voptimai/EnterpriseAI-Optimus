"""Connection lifecycle. Credentials never leave this service in API responses."""

import base64
import hashlib
import secrets
from datetime import timedelta
from sqlalchemy import delete, select
from app.config import get_settings
from app.models import User, utcnow
from app.models_connections import Connection, OAuthAttempt, CalendarEvent
from app.connectors.base import CredentialVault, ConnectorHTTP, validate_url
from app.connectors.microsoft import MicrosoftAdapter
from app.connectors.influxdb import InfluxAdapter
from app.connectors.zoom import ZoomAdapter
from app.repositories.connections import ConnectionRepository
from app.repositories.access import AccessRepository
from app.services.errors import ServiceError


class ConnectionService:
    def __init__(self, db, settings=None, http=None):
        self.db, self.settings, self.http = db, settings or get_settings(), http
        self.repo = ConnectionRepository(db)

    @property
    def vault(self):
        return CredentialVault(self.settings.credential_encryption_key)

    def credentials(self, connection):
        if connection.status == "disconnected" or not connection.encrypted_credentials:
            raise ServiceError("Reconnect this data source first.", 409)
        return self.vault.decrypt(connection.encrypted_credentials)

    def public(self, connection):
        return {
            key: getattr(connection, key)
            for key in (
                "id",
                "owner_id",
                "workspace_id",
                "provider",
                "name",
                "config",
                "status",
                "last_error",
                "last_synced_at",
                "next_sync_at",
                "created_at",
                "updated_at",
            )
        }

    def list(self, user, workspace_id=None):
        return [self.public(c) for c in self.repo.list(user, workspace_id)]

    def create(self, user, *, provider, name, config, credentials, workspace_id=None):
        AccessRepository(self.db).require_active(user)
        if workspace_id:
            AccessRepository(self.db).workspace(user, workspace_id, roles={"manager"})
        if provider not in {"influxdb", "zoom", "teams_workflow"}:
            raise ServiceError("Use the Microsoft authorization flow for Outlook.", 422)
        if provider == "influxdb":
            # Industrial endpoints are a privileged trust decision, independent of workspace roles.
            if not user.is_admin:
                raise ServiceError(
                    "An installation administrator must configure industrial endpoints.", 403
                )
            if not workspace_id:
                raise ServiceError("Industrial connections belong to a workspace.", 422)
            if set(config) - {"url", "org_id", "bucket_ids", "allow_private_network"}:
                raise ServiceError("Unknown InfluxDB configuration field.", 422)
            validate_url(
                config.get("url", ""),
                allow_private=bool(config.get("allow_private_network")),
                allow_query=False,
            )
            if (
                not credentials.get("token")
                or not isinstance(config.get("bucket_ids", []), list)
                or len(config.get("bucket_ids", [])) > 100
            ):
                raise ServiceError("Supply an InfluxDB read-only token and bucket ID list.", 422)
            config = {**config, "bucket_ids": config.get("bucket_ids", [])}
            credentials = {"token": credentials["token"]}
        elif provider == "zoom":
            if not all(credentials.get(k) for k in ("account_id", "client_id", "client_secret")):
                raise ServiceError("Zoom requires account ID, client ID and client secret.", 422)
            if not user.is_admin:
                raise ServiceError(
                    "An administrator must configure account-wide Zoom credentials.", 403
                )
            credentials = {k: credentials[k] for k in ("account_id", "client_id", "client_secret")}
            config = {}
        else:
            validate_url(
                credentials.get("webhook_url", ""),
                allowed_hosts=(
                    "logic.azure.com",
                    "api.powerplatform.com",
                    "environment.api.powerplatform.com",
                ),
            )
            credentials = {"webhook_url": credentials["webhook_url"]}
            config = {}
        obj = Connection(
            owner_id=user.id,
            workspace_id=workspace_id,
            provider=provider,
            name=name,
            config=config,
            encrypted_credentials=self.vault.encrypt(credentials),
            status="pending",
        )
        self.db.add(obj)
        self.db.flush()
        return obj

    def update(self, user, rid, *, name=None, config=None):
        obj = self.repo.get(user, rid, manage=True, lock=True)
        if name is not None:
            obj.name = name
        if config is not None:
            if obj.provider == "influxdb":
                if not user.is_admin:
                    raise ServiceError("An administrator must configure industrial endpoints.", 403)
                if set(config) - {"url", "org_id", "bucket_ids", "allow_private_network"}:
                    raise ServiceError("Unknown InfluxDB configuration field.", 422)
                merged = {**obj.config, **config}
                validate_url(
                    merged.get("url", ""),
                    allow_private=bool(merged.get("allow_private_network")),
                    allow_query=False,
                )
                if (
                    not isinstance(merged.get("bucket_ids", []), list)
                    or len(merged.get("bucket_ids", [])) > 100
                ):
                    raise ServiceError("Invalid bucket selection.", 422)
                obj.config = merged
            elif obj.provider == "microsoft":
                if (
                    set(config) - {"calendar_ids"}
                    or not isinstance(config.get("calendar_ids"), list)
                    or len(config["calendar_ids"]) > 20
                    or any(
                        not isinstance(c, str) or not c or len(c) > 1024
                        for c in config["calendar_ids"]
                    )
                ):
                    raise ServiceError("Choose up to 20 calendar IDs.", 422)
                obj.config = {**obj.config, **config}
                obj.last_synced_at = None
                obj.next_sync_at = utcnow()
            elif config:
                raise ServiceError("This provider has no editable public configuration.", 422)
        self.db.flush()
        return obj

    def disconnect(self, user, rid):
        obj = self.repo.get(user, rid, manage=True, lock=True)
        obj.status = "disconnected"
        obj.encrypted_credentials = ""
        obj.last_error = None
        obj.next_sync_at = None
        self.db.execute(delete(CalendarEvent).where(CalendarEvent.connection_id == obj.id))
        self.db.execute(delete(OAuthAttempt).where(OAuthAttempt.connection_id == obj.id))
        self.db.flush()
        return obj

    def start_microsoft(self, user, *, include_transcripts=False):
        state, verifier = secrets.token_urlsafe(48), secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        adapter = MicrosoftAdapter(self.settings, self.http)
        url = adapter.authorize_url(state, challenge, include_transcripts)
        obj = Connection(
            owner_id=user.id,
            provider="microsoft",
            name="Microsoft Outlook",
            config={"include_transcripts": include_transcripts, "calendar_ids": []},
            status="pending",
        )
        self.db.add(obj)
        self.db.flush()
        self.db.add(
            OAuthAttempt(
                connection_id=obj.id,
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                encrypted_verifier=self.vault.encrypt({"verifier": verifier}),
                expires_at=utcnow() + timedelta(minutes=10),
            )
        )
        self.db.execute(
            delete(OAuthAttempt).where(OAuthAttempt.expires_at < utcnow() - timedelta(days=1))
        )
        self.db.flush()
        return {"authorization_url": url, "connection_id": obj.id}

    async def finish_microsoft(self, *, state, code=None, error=None):
        attempt = self.db.scalar(
            select(OAuthAttempt)
            .where(OAuthAttempt.state_hash == hashlib.sha256(state.encode()).hexdigest())
            .with_for_update()
        )
        if not attempt or attempt.used_at or attempt.expires_at < utcnow():
            raise ServiceError(
                "Authorization expired or was already used. Start connection again.", 400
            )
        connection = self.db.get(Connection, attempt.connection_id)
        user = self.db.get(User, connection.owner_id)
        if not user or not user.active:
            raise ServiceError("This account is no longer active.", 403)
        attempt.used_at = utcnow()
        # One-use state is durably consumed even when the provider exchange fails.
        self.db.commit()
        if error or not code:
            connection.status = "error"
            connection.last_error = "Microsoft authorization was not completed."
            self.db.commit()
            return connection
        try:
            adapter = MicrosoftAdapter(self.settings, self.http)
            creds = await adapter.token(
                code=code, verifier=self.vault.decrypt(attempt.encrypted_verifier)["verifier"]
            )
            profile = await adapter.get(
                "/me",
                creds["access_token"],
                params={"$select": "id,displayName,mail,userPrincipalName"},
            )
            calendars = await adapter.calendars(creds["access_token"])
            creds["expires_at"] = (
                utcnow() + timedelta(seconds=int(creds.get("expires_in", 3600)))
            ).isoformat()
            connection.encrypted_credentials = self.vault.encrypt(creds)
            defaults = [c["id"] for c in calendars if c.get("isDefaultCalendar")]
            connection.config = {
                **connection.config,
                "account": profile.get("mail") or profile.get("userPrincipalName"),
                "calendar_ids": defaults or [c["id"] for c in calendars[:1]],
            }
            connection.status = "connected"
            connection.last_error = None
            connection.next_sync_at = utcnow()
        except ServiceError as exc:
            connection.status = "error"
            connection.last_error = exc.detail[:500]
        self.db.commit()
        return connection

    async def microsoft_token(self, connection):
        from datetime import datetime

        creds = self.credentials(connection)
        expiry = datetime.fromisoformat(creds.get("expires_at", "1970-01-01T00:00:00+00:00"))
        if expiry <= utcnow() + timedelta(minutes=2):
            if not creds.get("refresh_token"):
                raise ServiceError("Microsoft authorization expired. Reconnect Outlook.", 409)
            refreshed = await MicrosoftAdapter(self.settings, self.http).token(
                refresh_token=creds["refresh_token"]
            )
            creds.update(refreshed)
            creds["expires_at"] = (
                utcnow() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
            ).isoformat()
            connection.encrypted_credentials = self.vault.encrypt(creds)
        return creds["access_token"]

    async def resources(self, user, rid):
        obj = self.repo.get(user, rid, manage=True, lock=True)
        if obj.provider == "microsoft":
            result = {
                "calendars": await MicrosoftAdapter(self.settings, self.http).calendars(
                    await self.microsoft_token(obj)
                )
            }
        elif obj.provider == "influxdb":
            result = await InfluxAdapter(obj.config, self.credentials(obj), self.http).resources()
        elif obj.provider == "zoom":
            await ZoomAdapter(self.credentials(obj), self.http).token()
            result = {
                "capabilities": ["cloud_recording_transcripts"],
                "detail": "Supply a meeting ID to discover its completed artifacts.",
            }
        else:
            validate_url(
                self.credentials(obj)["webhook_url"],
                allowed_hosts=("logic.azure.com", "api.powerplatform.com"),
            )
            result = {
                "capabilities": ["teams_delivery"],
                "detail": "Endpoint validated. A test delivery verifies the Workflow.",
            }
        if obj.provider != "teams_workflow":
            obj.status = "connected"
        obj.last_error = None
        self.db.flush()
        return result

    async def query_series(
        self,
        user,
        connection_id,
        *,
        bucket_id,
        measurement,
        field,
        start,
        stop,
        limit=1000,
        aggregate_minutes=None,
        aggregation="mean",
        tags=None,
    ):
        obj = self.repo.get(user, connection_id)
        if obj.provider != "influxdb":
            raise ServiceError("Choose an InfluxDB connection.", 422)
        return await InfluxAdapter(obj.config, self.credentials(obj), self.http).query_series(
            bucket_id=bucket_id,
            measurement=measurement,
            field=field,
            start=start,
            stop=stop,
            limit=limit,
            aggregate_minutes=aggregate_minutes,
            aggregation=aggregation,
            tags=tags,
        )
