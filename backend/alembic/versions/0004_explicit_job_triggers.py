"""Distinguish intentional Run Now graphs from recurring schedule joins."""
from alembic import op
import sqlalchemy as sa
revision='0004_explicit_job_triggers'
down_revision='0003_meeting_extractions'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('job_runs',sa.Column('trigger_kind',sa.String(20),nullable=False,server_default=sa.text("'scheduled'")))
    op.create_check_constraint('ck_job_runs_trigger_kind','job_runs',"trigger_kind IN ('scheduled','manual')")

def downgrade():
    op.drop_constraint('ck_job_runs_trigger_kind','job_runs',type_='check')
    op.drop_column('job_runs','trigger_kind')
