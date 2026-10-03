"""Post-migration hardening for hosted PostgreSQL that offers a data API.

Hosted PostgreSQL services can publish schemas over HTTP (PostgREST-style data
APIs, such as Neon's Data API) to roles named `anonymous`/`anon` and
`authenticated`, and may grant new tables to them by default. Optimus never uses
such an API: it connects as the owner of its tables. When those roles exist, this
revokes their table/sequence access and enables row-level security with no
policies on every application table, so the API can neither read nor write
application data (users, sessions, documents, chats). Table owners bypass RLS, so
the application is unaffected. A database without such roles is left unchanged.
"""

from sqlalchemy import text

API_ROLES = ("anon", "anonymous", "authenticated")


def lock_down_schema(connection, schema: str = "public") -> dict:
    """Revoke Data API roles from the application schema and enable RLS on its tables."""
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
    target = f'"{schema}"'
    statements = [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {target} FROM {grantees}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {target} FROM {grantees}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {target} REVOKE ALL ON TABLES FROM {grantees}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {target} REVOKE ALL ON SEQUENCES FROM {grantees}",
    ]
    if schema != "public":
        # Platform services may rely on public staying usable; other schemas need not.
        statements.append(f"REVOKE ALL ON SCHEMA {target} FROM {grantees}")
    for statement in statements:
        connection.execute(text(statement))
    tables = (
        connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity"
            ),
            {"schema": schema},
        )
        .scalars()
        .all()
    )
    for table in tables:
        connection.execute(text(f'ALTER TABLE {target}."{table}" ENABLE ROW LEVEL SECURITY'))
    return {"api_roles": roles, "tables": len(tables)}
