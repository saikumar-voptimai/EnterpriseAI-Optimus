"""Connection lifecycle. Credentials never leave this service in API responses."""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from sqlalchemy import delete, select
from app.config import get_settings
from app.models import User, utcnow
from app.models_connections import Connection, OAuthAttempt, CalendarEvent
from app.connectors.base import CredentialVault, ConnectorHTTP, validate_url
from app.connectors.gdrive_folder import DriveFolder, folder_id_from, service_account
from app.connectors.google import GoogleAdapter
from app.connectors.postgres_source import PostgresSource
from app.connectors.microsoft import MicrosoftAdapter
from app.connectors.influxdb import InfluxAdapter
from app.connectors.zoom import ZoomAdapter
from app.repositories.connections import ConnectionRepository
from app.repositories.access import AccessRepository
from app.services.errors import ServiceError

DATA_SOURCES = {"influxdb", "postgres", "gdrive_folder"}
SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
ADMIN_ONLY_CONFIG = {
    "url",
    "org_id",
    "host",
    "port",
    "database",
    "sslmode",
    "service_account_email",
}


def slack_webhook(url):
    """Slack incoming webhooks only: https://hooks.slack.com/services/..."""
    checked = validate_url(url, allowed_hosts=("hooks.slack.com",))
    if not urlsplit(checked).path.startswith("/services/"):
        raise ServiceError("Use a Slack incoming webhook URL.", 422)
    return checked


def pkce_pair():
    state, verifier = secrets.token_urlsafe(48), secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return state, verifier, challenge


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
        rows = [self.public(c) for c in self.repo.list(user, workspace_id)]
        if not user.is_admin:
            # Endpoint addresses are an administrator concern, not a member's.
            for row in rows:
                row["config"] = {
                    k: v for k, v in row["config"].items() if k not in ADMIN_ONLY_CONFIG
                }
        return rows

    def create(self, user, *, provider, name, config, credentials, workspace_id=None):
        AccessRepository(self.db).require_active(user)
        if provider == "zoom" and workspace_id:
            raise ServiceError("Zoom is set up once for the organization in Administration.", 422)
        if workspace_id:
            AccessRepository(self.db).workspace(user, workspace_id, roles={"manager"})
        if provider not in {
            "influxdb",
            "zoom",
            "teams_workflow",
            "slack_webhook",
            "postgres",
            "gdrive_folder",
        }:
            raise ServiceError("Use the sign-in flow for calendar and file accounts.", 422)
        if provider in DATA_SOURCES:
            # Data sources are a privileged trust decision, independent of workspace roles.
            if not user.is_admin:
                raise ServiceError("An installation administrator must add data sources.", 403)
            if not workspace_id:
                raise ServiceError("Data sources belong to a workspace.", 422)
        if provider == "influxdb":
            # Industrial endpoints are a privileged trust decision, independent of workspace roles.
            if not user.is_admin:
                raise ServiceError(
                    "An installation administrator must configure industrial endpoints.", 403
                )
            if not workspace_id:
                raise ServiceError("Industrial connections belong to a workspace.", 422)
            if set(config) - {"url", "org_id", "bucket_ids", "allow_private_network"}:
                raise ServiceError("Unknown data source configuration field.", 422)
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
                raise ServiceError(
                    "Supply a read-only access token and the allowed data sets.", 422
                )
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
        elif provider == "postgres":
            config = {
                "host": str(config.get("host", "")).strip(),
                "port": int(config.get("port") or 5432),
                "database": str(config.get("database", "")).strip(),
                "sslmode": (
                    config.get("sslmode") if config.get("sslmode") in SSL_MODES else "prefer"
                ),
                "allow_private_network": bool(config.get("allow_private_network")),
                "tables": [],
            }
            if not credentials.get("username") or not credentials.get("password"):
                raise ServiceError("Enter the read-only database user and password.", 422)
            credentials = {"username": credentials["username"], "password": credentials["password"]}
            PostgresSource(config, credentials)  # Validates host, port and database name.
        elif provider == "gdrive_folder":
            config = {"folder_id": folder_id_from(config.get("folder_id", ""))}
            account = service_account(credentials.get("service_account", ""))
            credentials = {"service_account": account}
            config["service_account_email"] = account["client_email"]
        elif provider == "slack_webhook":
            slack_webhook(credentials.get("webhook_url", ""))
            credentials = {"webhook_url": credentials["webhook_url"]}
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
                    raise ServiceError("Unknown data source configuration field.", 422)
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
            elif obj.provider == "postgres":
                if not user.is_admin:
                    raise ServiceError("An administrator must configure data sources.", 403)
                tables = config.get("tables")
                if (
                    set(config) - {"tables"}
                    or not isinstance(tables, list)
                    or len(tables) > 200
                    or any(not isinstance(t, str) or "." not in t or len(t) > 300 for t in tables)
                ):
                    raise ServiceError("Choose up to 200 schema-qualified tables.", 422)
                obj.config = {**obj.config, "tables": list(dict.fromkeys(tables))}
            elif obj.provider in {"microsoft", "google"}:
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

    def _sign_in_connection(self, user, provider, name, config):
        """Reuse the person's active connection so repeated sign-ins never duplicate it."""
        existing = self.db.scalar(
            select(Connection)
            .where(
                Connection.owner_id == user.id,
                Connection.workspace_id.is_(None),
                Connection.provider == provider,
                Connection.status != "disconnected",
            )
            .with_for_update()
        )
        if existing:
            existing.config = {
                **config,
                **existing.config,
                **{k: v for k, v in config.items() if k != "calendar_ids"},
            }
            return existing
        obj = Connection(
            owner_id=user.id, provider=provider, name=name, config=config, status="pending"
        )
        self.db.add(obj)
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
        obj = self._sign_in_connection(
            user,
            "microsoft",
            "Microsoft 365",
            {"include_transcripts": include_transcripts, "calendar_ids": []},
        )
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
            if not connection.encrypted_credentials:
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
                "calendar_ids": connection.config.get("calendar_ids")
                or defaults
                or [c["id"] for c in calendars[:1]],
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

    def start_google(self, user):
        state, verifier, challenge = pkce_pair()
        url = GoogleAdapter(self.settings, self.http).authorize_url(state, challenge)
        obj = self._sign_in_connection(user, "google", "Google Workspace", {"calendar_ids": []})
        self.db.add(
            OAuthAttempt(
                connection_id=obj.id,
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                encrypted_verifier=self.vault.encrypt({"verifier": verifier}),
                expires_at=utcnow() + timedelta(minutes=10),
            )
        )
        self.db.flush()
        return {"authorization_url": url, "connection_id": obj.id}

    async def finish_google(self, *, state, code=None, error=None):
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
        if connection.provider != "google" or not user or not user.active:
            raise ServiceError("This authorization is no longer valid.", 403)
        attempt.used_at = utcnow()
        # One-use state is durably consumed even when the provider exchange fails.
        self.db.commit()
        if error or not code:
            if not connection.encrypted_credentials:
                connection.status = "error"
            connection.last_error = "Google authorization was not completed."
            self.db.commit()
            return connection
        try:
            adapter = GoogleAdapter(self.settings, self.http)
            creds = await adapter.token(
                code=code, verifier=self.vault.decrypt(attempt.encrypted_verifier)["verifier"]
            )
            calendars = await adapter.calendars(creds["access_token"])
            creds["expires_at"] = (
                utcnow() + timedelta(seconds=int(creds.get("expires_in", 3600)))
            ).isoformat()
            connection.encrypted_credentials = self.vault.encrypt(creds)
            primary = [c["id"] for c in calendars if c["primary"]]
            connection.config = {
                **connection.config,
                "account": await adapter.account(creds["access_token"]),
                "calendar_ids": connection.config.get("calendar_ids")
                or primary
                or [c["id"] for c in calendars[:1]],
            }
            connection.status = "connected"
            connection.last_error = None
            connection.next_sync_at = utcnow()
        except ServiceError as exc:
            connection.status = "error"
            connection.last_error = exc.detail[:500]
        self.db.commit()
        return connection

    async def google_token(self, connection):
        creds = self.credentials(connection)
        expiry = datetime.fromisoformat(creds.get("expires_at", "1970-01-01T00:00:00+00:00"))
        if expiry <= utcnow() + timedelta(minutes=2):
            if not creds.get("refresh_token"):
                raise ServiceError("Google authorization expired. Reconnect Google.", 409)
            refreshed = await GoogleAdapter(self.settings, self.http).token(
                refresh_token=creds["refresh_token"]
            )
            # Google omits the refresh token on refresh; keep the stored one.
            creds.update({k: v for k, v in refreshed.items() if v})
            creds["expires_at"] = (
                utcnow() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
            ).isoformat()
            connection.encrypted_credentials = self.vault.encrypt(creds)
        return creds["access_token"]

    def _google(self, user, connection_id):
        obj = self.repo.get(user, connection_id, manage=True, lock=True)
        if obj.provider != "google":
            raise ServiceError("Choose a Google connection.", 422)
        return obj

    async def drive_files(self, user, connection_id, search=""):
        obj = self._google(user, connection_id)
        return await GoogleAdapter(self.settings, self.http).drive_files(
            await self.google_token(obj), search[:200]
        )

    async def import_drive_file(
        self, user, connection_id, file_id, workspace_id=None, project_id=None
    ):
        from app.services.ingestion import IngestionService

        obj = self._google(user, connection_id)
        if not file_id or len(file_id) > 200:
            raise ServiceError("Choose a file to import.", 422)
        filename, content = await GoogleAdapter(self.settings, self.http).drive_download(
            await self.google_token(obj), file_id, self.settings.max_upload_bytes
        )
        return IngestionService(self.db, self.settings).ingest(
            user, content, filename, workspace_id=workspace_id, project_id=project_id
        )

    async def resources(self, user, rid):
        obj = self.repo.get(user, rid, manage=True, lock=True)
        if obj.provider == "microsoft":
            result = {
                "calendars": await MicrosoftAdapter(self.settings, self.http).calendars(
                    await self.microsoft_token(obj)
                )
            }
        elif obj.provider == "google":
            result = {
                "calendars": await GoogleAdapter(self.settings, self.http).calendars(
                    await self.google_token(obj)
                )
            }
        elif obj.provider == "slack_webhook":
            slack_webhook(self.credentials(obj)["webhook_url"])
            result = {"capabilities": ["slack_delivery"], "detail": "Ready for notifications."}
        elif obj.provider == "postgres":
            result = await PostgresSource(obj.config, self.credentials(obj)).resources()
        elif obj.provider == "gdrive_folder":
            drive = DriveFolder(obj.config, self.credentials(obj), self.http)
            folder, files = await drive.folder(), await drive.files(limit=50)
            obj.config = {**obj.config, "folder_name": folder["name"]}
            result = {"folder": folder, "files": files}
        elif obj.provider == "influxdb":
            result = await InfluxAdapter(obj.config, self.credentials(obj), self.http).resources()
            organizations = result["organizations"]
            if user.is_admin and not obj.config.get("org_id") and len(organizations) == 1:
                # A single reachable organization needs no choice.
                obj.config = {**obj.config, "org_id": organizations[0]["id"]}
            # Remember data set names so forms and the assistant can show names, not IDs.
            names = {b["id"]: b["name"][:200] for b in result["buckets"][:100]}
            if names != obj.config.get("bucket_names"):
                obj.config = {**obj.config, "bucket_names": names}
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
        if obj.provider not in {"teams_workflow", "slack_webhook"}:
            obj.status = "connected"
        obj.last_error = None
        self.db.flush()
        return result

    def _influx(self, user, connection_id):
        obj = self.repo.get(user, connection_id)
        if obj.provider != "influxdb":
            raise ServiceError("Choose a process data connection.", 422)
        return InfluxAdapter(obj.config, self.credentials(obj), self.http)

    async def bucket_names(self, user, connection_id):
        return await self._influx(user, connection_id).bucket_names()

    async def describe_series(
        self, user, connection_id, *, bucket_id, measurement=None, lookback_days=365
    ):
        return await self._influx(user, connection_id).describe(
            bucket_id=bucket_id, measurement=measurement, lookback_days=lookback_days
        )

    def _source(self, user, connection_id, provider):
        obj = self.repo.get(user, connection_id)
        if obj.provider != provider:
            raise ServiceError("Choose a matching data source.", 422)
        return obj

    async def describe_table(self, user, connection_id, table):
        obj = self._source(user, connection_id, "postgres")
        return await PostgresSource(obj.config, self.credentials(obj)).describe(table)

    async def query_table(self, user, connection_id, table, **request):
        obj = self._source(user, connection_id, "postgres")
        return await PostgresSource(obj.config, self.credentials(obj)).query(table, **request)

    async def folder_files(self, user, connection_id, search=""):
        obj = self._source(user, connection_id, "gdrive_folder")
        return await DriveFolder(obj.config, self.credentials(obj), self.http).files(search[:200])

    async def read_spreadsheet(self, user, connection_id, file_id, **window):
        obj = self._source(user, connection_id, "gdrive_folder")
        return await DriveFolder(obj.config, self.credentials(obj), self.http).read_table(
            file_id, **window
        )

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
            raise ServiceError("Choose a process data connection.", 422)
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
