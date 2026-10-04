"""SQL and Google Drive folder data sources; one active personal sign-in per provider."""

from alembic import op

revision = "0006_data_sources"
down_revision = "0005_google_slack"
branch_labels = None
depends_on = None

PROVIDERS_OLD = (
    "provider IN ('microsoft','zoom','influxdb','teams_workflow','google','slack_webhook')"
)
PROVIDERS_NEW = (
    "provider IN ('microsoft','zoom','influxdb','teams_workflow','google','slack_webhook',"
    "'postgres','gdrive_folder')"
)
DUPLICATES = """
    SELECT id FROM (
        SELECT id, row_number() OVER (
            PARTITION BY owner_id, provider
            ORDER BY (status = 'connected') DESC, created_at DESC
        ) AS rank
        FROM connections
        WHERE provider IN ('google', 'microsoft')
          AND workspace_id IS NULL
          AND status <> 'disconnected'
    ) ranked WHERE rank > 1
"""


def upgrade():
    op.drop_constraint(op.f("ck_connections_provider"), "connections", type_="check")
    op.create_check_constraint(op.f("ck_connections_provider"), "connections", PROVIDERS_NEW)
    # Repeated sign-ins created parallel connections whose calendars duplicated
    # events; keep the newest working one and retire the rest.
    op.execute(f"DELETE FROM calendar_events WHERE connection_id IN ({DUPLICATES})")
    op.execute(f"DELETE FROM oauth_attempts WHERE connection_id IN ({DUPLICATES})")
    op.execute(
        "UPDATE connections SET status = 'disconnected', encrypted_credentials = '', "
        f"next_sync_at = NULL, last_error = NULL WHERE id IN ({DUPLICATES})"
    )
    op.create_index(
        op.f("uq_connections_personal_sign_in"),
        "connections",
        ["owner_id", "provider"],
        unique=True,
        postgresql_where="workspace_id IS NULL AND provider IN ('google', 'microsoft') "
        "AND status <> 'disconnected'",
    )


def downgrade():
    op.drop_index(op.f("uq_connections_personal_sign_in"), table_name="connections")
    op.drop_constraint(op.f("ck_connections_provider"), "connections", type_="check")
    op.create_check_constraint(op.f("ck_connections_provider"), "connections", PROVIDERS_OLD)
