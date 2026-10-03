"""Google Workspace connections and Slack delivery."""

from alembic import op

revision = "0005_google_slack"
down_revision = "0004_explicit_job_triggers"
branch_labels = None
depends_on = None

PROVIDERS_OLD = "provider IN ('microsoft','zoom','influxdb','teams_workflow')"
PROVIDERS_NEW = (
    "provider IN ('microsoft','zoom','influxdb','teams_workflow','google','slack_webhook')"
)
CHANNELS_OLD = "channel IN ('email','teams')"
CHANNELS_NEW = "channel IN ('email','teams','slack')"


def _replace(table, name, condition):
    # op.f(): the names are final; do not re-apply the naming convention.
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, condition)


def upgrade():
    _replace("connections", "ck_connections_provider", PROVIDERS_NEW)
    _replace("deliveries", "ck_deliveries_channel", CHANNELS_NEW)


def downgrade():
    _replace("deliveries", "ck_deliveries_channel", CHANNELS_OLD)
    _replace("connections", "ck_connections_provider", PROVIDERS_OLD)
