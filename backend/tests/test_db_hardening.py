"""Hosted data API lockdown, simulated with API roles inside a rolled-back transaction."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from app.db_hardening import lock_down_schema

API_ROLES = ("anon", "authenticated")


def scalar(connection, sql, **params):
    return connection.execute(text(sql), params).scalar()


def test_api_roles_lose_table_access_and_every_table_gets_rls(db_engine):
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            created = []
            for role in API_ROLES:
                if not scalar(connection, "SELECT 1 FROM pg_roles WHERE rolname = :r", r=role):
                    connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
                    created.append(role)
                # A hosted platform's default grants on the public schema.
                connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                connection.execute(text(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {role}"))
            assert scalar(
                connection, "SELECT has_table_privilege('anon', 'public.users', 'SELECT')"
            )

            result = lock_down_schema(connection)

            # Hosts may define further API roles (Neon also has "anonymous").
            assert set(API_ROLES) <= set(result["api_roles"])
            for role in API_ROLES:
                for table, privilege in (("users", "SELECT"), ("auth_sessions", "INSERT")):
                    assert not scalar(
                        connection,
                        f"SELECT has_table_privilege('{role}', 'public.{table}', '{privilege}')",
                    )
            assert (
                scalar(
                    connection,
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity",
                )
                == 0
            )
            if "anon" in created:
                # Act as the API role. A non-superuser test owner needs membership first.
                connection.execute(text("GRANT anon TO CURRENT_USER"))
                savepoint = connection.begin_nested()
                connection.execute(text("SET LOCAL ROLE anon"))
                with pytest.raises(ProgrammingError, match="permission denied"):
                    connection.execute(text("SELECT count(*) FROM users"))
                savepoint.rollback()
            # The owning application role is unaffected.
            assert scalar(connection, "SELECT count(*) FROM users") >= 0
            # A second run (every migration) is a no-op for already protected tables.
            assert lock_down_schema(connection)["tables"] == 0
        finally:
            transaction.rollback()


def test_plain_postgres_without_api_roles_is_untouched(db_engine):
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            if scalar(
                connection,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'anonymous', 'authenticated')",
            ):
                pytest.skip("This test database already defines data API roles")
            assert lock_down_schema(connection) == {"api_roles": [], "tables": 0}
            assert not scalar(
                connection,
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.users'::regclass",
            )
        finally:
            transaction.rollback()


def test_migrations_can_build_a_separate_droppable_schema(db_engine, monkeypatch):
    """DATABASE_SCHEMA=<name> keeps an environment (such as a demo) in its own schema."""
    import os
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from app import config as app_config

    schema = "pytest_env_schema"
    backend = Path(__file__).resolve().parents[1]
    with db_engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    app_config.get_settings.cache_clear()
    try:
        alembic_config = Config(str(backend / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(backend / "alembic"))
        command.upgrade(alembic_config, "head")
        count = "SELECT count(*) FROM pg_tables WHERE schemaname = :s"
        with db_engine.connect() as connection:
            in_schema = connection.execute(text(count), {"s": schema}).scalar()
            in_public = connection.execute(text(count), {"s": "public"}).scalar()
            version = connection.execute(
                text(f"SELECT version_num FROM {schema}.alembic_version")
            ).scalar()
            public_version = connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar()
        assert in_schema == in_public > 50 and version == public_version
        options = app_config.get_settings().connect_options
        engine = create_engine(os.environ["DATABASE_URL"], connect_args={"options": options})
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_schema()")).scalar() == schema
            assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 0
        engine.dispose()
    finally:
        with db_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        app_config.get_settings.cache_clear()
