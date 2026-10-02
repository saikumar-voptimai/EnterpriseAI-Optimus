"""Supabase Data API lockdown, simulated with its roles inside a rolled-back transaction."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from app.db_hardening import lock_down_public_schema

API_ROLES = ("anon", "authenticated")


def scalar(connection, sql, **params):
    return connection.execute(text(sql), params).scalar()


def test_api_roles_lose_table_access_and_every_table_gets_rls(db_engine):
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for role in API_ROLES:
                if not scalar(connection, "SELECT 1 FROM pg_roles WHERE rolname = :r", r=role):
                    connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
                # Supabase's default grants on the public schema.
                connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                connection.execute(text(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {role}"))
            assert scalar(
                connection, "SELECT has_table_privilege('anon', 'public.users', 'SELECT')"
            )

            result = lock_down_public_schema(connection)

            assert result["api_roles"] == list(API_ROLES) and result["tables"] > 0
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
            savepoint = connection.begin_nested()
            connection.execute(text("SET LOCAL ROLE anon"))
            with pytest.raises(ProgrammingError, match="permission denied"):
                connection.execute(text("SELECT count(*) FROM users"))
            savepoint.rollback()
            # The owning application role is unaffected.
            assert scalar(connection, "SELECT count(*) FROM users") >= 0
            # A second run (every migration) is a no-op for already protected tables.
            assert lock_down_public_schema(connection)["tables"] == 0
        finally:
            transaction.rollback()


def test_plain_postgres_without_api_roles_is_untouched(db_engine):
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            if scalar(
                connection,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'authenticated')",
            ):
                pytest.skip("This test database already defines Supabase API roles")
            assert lock_down_public_schema(connection) == {"api_roles": [], "tables": 0}
            assert not scalar(
                connection,
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.users'::regclass",
            )
        finally:
            transaction.rollback()
