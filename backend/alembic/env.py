"""Alembic environment. DATABASE_URL is the same setting used by the API/worker."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import get_settings
from app.db_hardening import lock_down_public_schema
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        get_settings().database_url,
        poolclass=pool.NullPool,
        connect_args={"options": "-c timezone=UTC"},
    )
    with connectable.connect() as connection:
        # Revision 0002 stores pgvector columns. Managed PostgreSQL (Neon, Supabase) and
        # the pgvector image ship the extension but do not enable it in a new database.
        # Supabase keeps extensions in its `extensions` schema (on the search_path)
        # rather than in the API-exposed public schema.
        extensions_schema = connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = 'extensions'")
        ).scalar()
        connection.execute(
            text(
                "CREATE EXTENSION IF NOT EXISTS vector"
                + (" WITH SCHEMA extensions" if extensions_schema else "")
            )
        )
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
            # Supabase exposes the public schema via its Data API; close it in the
            # same transaction so no migrated table is ever readable through it.
            lock_down_public_schema(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
