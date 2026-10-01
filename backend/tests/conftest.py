"""Integration tests require an explicitly disposable PostgreSQL test database.

TEST_DATABASE_URL is intentionally separate from a developer's application URL.
Every test truncates application tables; never point it at a deployment database.
"""

import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

TEST_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL

from app.config import Settings

# A developer's .env (Neon URL, real keys, larger limits) must never shape tests.
Settings.model_config["env_file"] = None
# Fixed test settings prevent a developer's real provider credentials being used.
os.environ.update(
    {
        "APP_ORIGIN": "http://testserver",
        "SESSION_COOKIE_SECURE": "false",
        "ALLOW_EXTERNAL_AI": "true",
        "OPENROUTER_API_KEY": "test-key-never-sent",
        "OPENROUTER_MODELS": "test/model-a,test/model-b",
        "OPENROUTER_DEFAULT_MODEL": "test/model-a",
        "OPENROUTER_MODEL_FAST": "",
        "OPENROUTER_MODEL_STANDARD": "",
        "OPENROUTER_MODEL_DEEP": "",
        "OPENROUTER_SYSTEM1_MODEL": "",
        "VECTOR_BACKEND": "pgvector",
    }
)


@pytest.fixture(scope="session")
def db_engine():
    if not TEST_URL:
        pytest.skip("Set TEST_DATABASE_URL to an empty, disposable PostgreSQL database")
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(config, "head")
    engine = create_engine(
        TEST_URL,
        poolclass=NullPool,
        connect_args={"options": "-c timezone=UTC", "prepare_threshold": None},
    )
    from app import db as application_db

    application_db.engine.dispose()
    application_db.engine = engine
    application_db.SessionLocal.configure(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_factory(db_engine):
    from app.models import Base

    table_names = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
    with db_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
    return sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def db(db_factory):
    with db_factory() as session:
        yield session


@pytest.fixture
def client(db_factory):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(
        app, base_url="http://testserver", headers={"Origin": "http://testserver"}
    ) as result:
        yield result
