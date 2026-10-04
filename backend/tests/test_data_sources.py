"""Read-only SQL and Google Drive folder data sources, organization Zoom and sign-in reuse."""

import asyncio
import base64
import io
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from app.connectors.base import ConnectorHTTP, CredentialVault
from app.connectors.gdrive_folder import DriveFolder, folder_id_from, service_account
from app.connectors.postgres_source import PostgresSource
from app.models import utcnow
from app.models_connections import Connection
from app.repositories import NotFound
from app.services.connections import ConnectionService
from app.services.errors import ProviderError, ServiceError
from test_connections import people, settings

READER, READER_PASSWORD = "optimus_reader_test", "reader-test-pass-1"


@pytest.fixture
def source_db(db_engine):
    """A customer database: schema with a shared table, a view and a private table."""
    with db_engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS plant_src CASCADE"))
        c.execute(text(f"DROP ROLE IF EXISTS {READER}"))
        c.execute(text("CREATE SCHEMA plant_src"))
        c.execute(
            text(
                "CREATE TABLE plant_src.readings (id serial PRIMARY KEY, unit text, temp numeric, "
                "at timestamptz DEFAULT now())"
            )
        )
        c.execute(
            text(
                "INSERT INTO plant_src.readings (unit, temp) VALUES "
                "('BF1', 1180.5), ('BF1', 1190.5), ('BF2', 1205), ('BF2', 1215), ('BF2_old', 900)"
            )
        )
        c.execute(
            text("CREATE VIEW plant_src.hot AS SELECT * FROM plant_src.readings WHERE temp > 1200")
        )
        c.execute(text("CREATE TABLE plant_src.salaries (name text, amount numeric)"))
        c.execute(text(f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PASSWORD}'"))
        c.execute(text(f"GRANT USAGE ON SCHEMA plant_src TO {READER}"))
        c.execute(text(f"GRANT SELECT ON plant_src.readings, plant_src.hot TO {READER}"))
    url = make_url(os.environ["TEST_DATABASE_URL"])
    config = {
        "host": url.host,
        "port": url.port or 5432,
        "database": url.database,
        "sslmode": "prefer",
        "allow_private_network": True,
        "tables": ["plant_src.readings", "plant_src.hot"],
    }
    yield config, {"username": READER, "password": READER_PASSWORD}
    with db_engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS plant_src CASCADE"))
        c.execute(text(f"DROP OWNED BY {READER}"))
        c.execute(text(f"DROP ROLE IF EXISTS {READER}"))


def test_sql_source_lists_only_readable_tables_and_describes_them(source_db):
    config, creds = source_db
    source = PostgresSource(config, creds)
    tables = {t["id"]: t["kind"] for t in asyncio.run(source.resources())["tables"]}
    assert tables.get("plant_src.readings") == "table" and tables.get("plant_src.hot") == "view"
    assert "plant_src.salaries" not in tables
    described = asyncio.run(source.describe("plant_src.readings"))
    assert [c["name"] for c in described["columns"]] == ["id", "unit", "temp", "at"]
    assert len(described["sample"]) == 5 and isinstance(described["sample"][0]["temp"], float)


def test_sql_source_generates_bounded_filtered_and_aggregated_reads(source_db):
    config, creds = source_db
    source = PostgresSource(config, creds)
    rows = asyncio.run(
        source.query(
            "plant_src.readings",
            columns=["unit", "temp"],
            filters=[
                {"column": "unit", "op": "contains", "value": "BF2"},
                {"column": "temp", "op": ">", "value": 1000},
            ],
            order_by="temp",
            descending=True,
            limit=1,
        )
    )
    assert rows["rows"] == [{"unit": "BF2", "temp": 1215.0}] and rows["truncated"] is True
    grouped = asyncio.run(
        source.query(
            "plant_src.readings",
            aggregate={"function": "avg", "column": "temp"},
            group_by=["unit"],
            filters=[{"column": "unit", "op": "!=", "value": "BF2_old"}],
        )
    )
    assert {r["unit"]: r["value"] for r in grouped["rows"]} == {"BF2": 1210.0, "BF1": 1185.5}


def test_sql_source_rejects_unknown_names_unshared_tables_writes_and_private_hosts(source_db):
    config, creds = source_db
    source = PostgresSource(config, creds)
    with pytest.raises(ServiceError, match="Unknown column"):
        asyncio.run(
            source.query("plant_src.readings", columns=['temp"; DROP TABLE plant_src.readings; --'])
        )
    with pytest.raises(ServiceError, match="not shared"):
        asyncio.run(source.query("plant_src.salaries"))
    with pytest.raises(ProviderError, match="rejected the query"):
        source._run(lambda conn: conn.execute("CREATE TABLE plant_src.injected (i int)"))
    with pytest.raises(ServiceError, match="Private endpoints"):
        PostgresSource({**config, "allow_private_network": False}, creds)


def signing_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    account = {
        "type": "service_account",
        "client_email": "optimus-reader@demo-project.iam.gserviceaccount.com",
        "private_key": pem,
        "private_key_id": "kid-1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return key, account


def workbook_bytes():
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Daily"
    sheet.append(["Date", "Hot metal (t)", "Coke rate"])
    for day in range(1, 31):
        sheet.append([f"2026-09-{day:02d}", 3200 + day, 480.5])
    book.create_sheet("Notes").append(["free text"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def drive_responder(key, seen):
    folder, sub = "FOLDER1234567", "SUBFOLDER123"

    def respond(request):
        path, params = request.url.path, request.url.params
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            header, claims, signature = form["assertion"][0].split(".")
            pad = lambda part: part + "=" * (-len(part) % 4)
            key.public_key().verify(
                base64.urlsafe_b64decode(pad(signature)),
                f"{header}.{claims}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            seen["claims"] = json.loads(base64.urlsafe_b64decode(pad(claims)))
            return httpx.Response(200, json={"access_token": "sa-token"})
        assert request.headers["authorization"] == "Bearer sa-token"
        if path == "/drive/v3/files":
            parent = params["q"].split("'")[1]
            items = {
                folder: [
                    {
                        "id": sub,
                        "name": "Blast furnace",
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                    {
                        "id": "csv1",
                        "name": "shift.csv",
                        "mimeType": "text/csv",
                        "modifiedTime": "2026-10-01T00:00:00Z",
                    },
                ],
                sub: [
                    {
                        "id": "sheet1",
                        "name": "BF2 daily",
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                        "modifiedTime": "2026-10-03T06:00:00Z",
                    },
                ],
            }.get(parent, [])
            return httpx.Response(200, json={"files": items})
        if path.endswith("/export"):
            return httpx.Response(200, content=workbook_bytes())
        file_id = path.rsplit("/", 1)[-1]
        meta = {
            folder: {
                "id": folder,
                "name": "Plant reports",
                "mimeType": "application/vnd.google-apps.folder",
            },
            sub: {
                "id": sub,
                "name": "Blast furnace",
                "parents": [folder],
                "mimeType": "application/vnd.google-apps.folder",
            },
            "sheet1": {
                "id": "sheet1",
                "name": "BF2 daily",
                "parents": [sub],
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-10-03T06:00:00Z",
            },
            "outside": {
                "id": "outside",
                "name": "private",
                "parents": ["SOMEONE_ELSE_FOLDER"],
                "mimeType": "application/vnd.google-apps.spreadsheet",
            },
        }.get(file_id)
        return (
            httpx.Response(200, json=meta)
            if meta
            else httpx.Response(200, json={"id": file_id, "parents": []})
        )

    return respond


def test_drive_folder_reads_spreadsheets_only_inside_the_shared_folder():
    key, account = signing_key()
    seen = {}
    drive = DriveFolder(
        {"folder_id": "https://drive.google.com/drive/folders/FOLDER1234567?usp=sharing"},
        {"service_account": account},
        ConnectorHTTP(httpx.MockTransport(drive_responder(key, seen))),
    )
    assert asyncio.run(drive.folder()) == {"id": "FOLDER1234567", "name": "Plant reports"}
    files = asyncio.run(drive.files())
    assert [f["name"] for f in files] == ["Blast furnace/BF2 daily", "shift.csv"]
    assert seen["claims"]["iss"] == account["client_email"] and seen["claims"]["scope"].endswith(
        "drive.readonly"
    )
    table = asyncio.run(drive.read_table("sheet1", offset=25, limit=10))
    assert table["sheets"] == ["Daily", "Notes"] and table["columns"] == [
        "Date",
        "Hot metal (t)",
        "Coke rate",
    ]
    assert table["total_rows"] == 30 and len(table["rows"]) == 5 and table["truncated"] is False
    assert table["rows"][0] == {"Date": "2026-09-26", "Hot metal (t)": 3226, "Coke rate": 480.5}
    with pytest.raises(ServiceError, match="not in the shared folder"):
        asyncio.run(drive.read_table("outside"))


def test_drive_folder_inputs_are_validated():
    assert (
        folder_id_from("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjK_lm-no")
        == "1AbCdEfGhIjK_lm-no"
    )
    with pytest.raises(ServiceError, match="folder link"):
        folder_id_from("https://example.com/")
    with pytest.raises(ServiceError, match="service account"):
        service_account(json.dumps({"type": "authorized_user", "client_email": "me@gmail.com"}))


def test_repeated_google_sign_in_reuses_one_connection(db):
    user, _ = people(db)
    cfg = SimpleNamespace(
        **{**vars(settings()), "google_client_id": "g", "google_client_secret": "s"}
    )
    service = ConnectionService(db, cfg)
    first = service.start_google(user)["connection_id"]
    second = service.start_google(user)["connection_id"]
    db.commit()
    assert first == second
    assert len(db.scalars(select(Connection).where(Connection.provider == "google")).all()) == 1
    db.add(Connection(owner_id=user.id, provider="google", name="Dup", config={}, status="pending"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_company_zoom_is_usable_by_everyone_but_managed_by_admins(db):
    from app.api_meetings import require_zoom_host

    admin, member = people(db)  # first person is an administrator
    zoom = ConnectionService(db, settings()).create(
        admin,
        provider="zoom",
        name="Company Zoom",
        config={},
        credentials={"account_id": "a", "client_id": "b", "client_secret": "c"},
    )
    db.commit()
    service = ConnectionService(db, settings())
    assert zoom.id in {c.id for c in service.repo.list(member)}
    assert service.repo.get(member, zoom.id).id == zoom.id
    with pytest.raises(NotFound):
        service.repo.get(member, zoom.id, manage=True)
    require_zoom_host(member, {"host_email": member.email.upper()})
    require_zoom_host(admin, {"host_email": "someone@else.test"})
    with pytest.raises(ServiceError, match="meeting host"):
        require_zoom_host(member, {"host_email": "someone@else.test"})
    with pytest.raises(ServiceError, match="organization"):
        ConnectionService(db, settings()).create(
            admin,
            provider="zoom",
            name="Team Zoom",
            workspace_id="00000000-0000-0000-0000-000000000000",
            config={},
            credentials={"account_id": "a", "client_id": "b", "client_secret": "c"},
        )


def test_assistant_reads_a_shared_sql_table_only_in_its_workspace(db, db_factory, source_db):
    from app.agents.tools import ToolRegistry
    from app.config import Settings
    from test_knowledge import seed

    config, creds = source_db
    user, other, workspace = seed(db)
    key = settings().credential_encryption_key
    conn = Connection(
        owner_id=user.id,
        workspace_id=workspace.id,
        provider="postgres",
        name="Plant database",
        status="connected",
        config=config,
        encrypted_credentials=CredentialVault(key).encrypt(creds),
    )
    db.add(conn)
    db.commit()
    cfg = Settings(_env_file=None, allow_external_ai=True, credential_encryption_key=key)
    tools = ToolRegistry(db_factory, cfg, user.id, workspace_id=workspace.id)
    listed = asyncio.run(tools.invoke("list_data_connections", "{}")).data
    assert listed == [
        {
            "id": conn.id,
            "name": "Plant database",
            "kind": "sql_database",
            "tables": config["tables"],
        }
    ]
    result = asyncio.run(
        tools.invoke(
            "query_table",
            json.dumps(
                {
                    "connection_id": conn.id,
                    "table": "plant_src.readings",
                    "aggregate": {"function": "count"},
                }
            ),
        )
    )
    assert result.data["rows"] == [{"value": 5}] and result.sources[0]["read_only"] is True
    with pytest.raises(NotFound):
        asyncio.run(
            ToolRegistry(db_factory, cfg, other.id, workspace_id=workspace.id).invoke(
                "query_table", json.dumps({"connection_id": conn.id, "table": "plant_src.readings"})
            )
        )
