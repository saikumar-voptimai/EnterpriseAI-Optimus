"""Alembic environment. DATABASE_URL is the same setting used by the API/worker."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import get_settings
from app.db_hardening import lock_down_schema
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
    settings = get_settings()
    schema = settings.database_schema
    connectable = create_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args={"options": settings.connect_options},
    )
    with connectable.connect() as connection:
        if schema != "public":
            # The connection's search_path puts this schema first, so unqualified
            # migration DDL creates every table, type and index inside it.
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # Revision 0002 stores pgvector columns. Managed PostgreSQL and the pgvector
        # image ship the extension but do not enable it in a new database. Where an
        # `extensions` schema exists (on the search_path), keep it out of public.
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=schema,
        )
        with context.begin_transaction():
            context.run_migrations()
            # Hosted data APIs can expose schemas; close the application schema in the
            # same transaction so no migrated table is ever readable through one.
            lock_down_schema(connection, schema)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
