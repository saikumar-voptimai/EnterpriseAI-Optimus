"""Post-migration hardening for managed PostgreSQL that exposes a data API.

Supabase publishes the `public` schema through its Data API (PostgREST and
GraphQL) to the `anon` and `authenticated` roles. The anon key is designed to be
public, and new tables there are granted to those roles by default. Optimus never
uses that API: it connects as the owner of its tables. When those roles exist,
this revokes their table/sequence access and enables row-level security with no
policies on every table, so the API can neither read nor write application data
(users, sessions, documents, chats). Table owners bypass RLS, so the application
is unaffected. Plain PostgreSQL (Docker, Neon) has no such roles and is unchanged.
"""

from sqlalchemy import text

API_ROLES = ("anon", "authenticated")


def lock_down_public_schema(connection) -> dict:
    roles = [
        role
        for role in API_ROLES
        if connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        ).scalar()
    ]
    if not roles:
        return {"api_roles": [], "tables": 0}
    grantees = ", ".join(roles)
    for statement in (
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {grantees}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {grantees}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {grantees}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {grantees}",
    ):
        connection.execute(text(statement))
    tables = (
        connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity"
            )
        )
        .scalars()
        .all()
    )
    for table in tables:
        connection.execute(text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
    return {"api_roles": roles, "tables": len(tables)}
