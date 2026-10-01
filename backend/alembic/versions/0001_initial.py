"""Create the normalized v1 schema.

This migration is an immutable snapshot and intentionally does not import models.
Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_app_settings')),
    sa.UniqueConstraint('key', name='uq_app_settings_key')
    )
    op.create_table('login_attempts',
    sa.Column('key', sa.String(length=320), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_login_attempts_nonnegative_attempts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_login_attempts')),
    sa.UniqueConstraint('key', name='uq_login_attempts_key')
    )
    op.create_index('ix_login_attempts_window_start', 'login_attempts', ['window_start'], unique=False)
    op.create_table('scopes',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=80), nullable=False),
    sa.Column('parent_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('parent_id IS NULL OR parent_id <> id', name=op.f('ck_scopes_not_own_parent')),
    sa.ForeignKeyConstraint(['parent_id'], ['scopes.id'], name=op.f('fk_scopes_parent_id_scopes'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scopes'))
    )
    op.create_index(op.f('ix_scopes_parent_id'), 'scopes', ['parent_id'], unique=False)
    op.create_index('uq_scopes_name_parent', 'scopes', ['parent_id', 'name'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('clearance', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('clearance BETWEEN 1 AND 3', name=op.f('ck_users_clearance')),
    sa.CheckConstraint('email = lower(email) AND length(email) > 3', name=op.f('ck_users_email_lowercase')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name='uq_users_email')
    )
    op.create_table('audit_events',
    sa.Column('actor_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('resource_type', sa.String(length=80), nullable=False),
    sa.Column('resource_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_events_actor_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_index(op.f('ix_audit_events_actor_id'), 'audit_events', ['actor_id'], unique=False)
    op.create_index('ix_audit_events_resource', 'audit_events', ['resource_type', 'resource_id'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('user_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('csrf_token', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('token_hash', name='uq_auth_sessions_token_hash')
    )
    op.create_index('ix_auth_sessions_expires_at', 'auth_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('notifications',
    sa.Column('user_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('kind', sa.String(length=80), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('resource_type', sa.String(length=80), nullable=False),
    sa.Column('resource_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dedupe_key', sa.String(length=500), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('dedupe_key', name='uq_notifications_dedupe_key')
    )
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_index('ix_notifications_user_read_created', 'notifications', ['user_id', 'read_at', 'created_at'], unique=False)
    op.create_table('projects',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('goal', sa.Text(), server_default=sa.text("''"), nullable=False),
    sa.Column('archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_projects_owner_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects')),
    sa.UniqueConstraint('id', 'owner_id', name='uq_projects_id_owner')
    )
    op.create_index(op.f('ix_projects_owner_id'), 'projects', ['owner_id'], unique=False)
    op.create_table('scope_grants',
    sa.Column('user_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('scope_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('can_review', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.id'], name=op.f('fk_scope_grants_scope_id_scopes'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_scope_grants_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'scope_id', name=op.f('pk_scope_grants'))
    )
    op.create_index(op.f('ix_scope_grants_scope_id'), 'scope_grants', ['scope_id'], unique=False)
    op.create_table('subscriptions',
    sa.Column('user_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('scope_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.id'], name=op.f('fk_subscriptions_scope_id_scopes'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_subscriptions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'scope_id', name=op.f('pk_subscriptions'))
    )
    op.create_index(op.f('ix_subscriptions_scope_id'), 'subscriptions', ['scope_id'], unique=False)
    op.create_table('workspaces',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), server_default=sa.text("''"), nullable=False),
    sa.Column('scope_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('classification', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('external_ai_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_by', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('classification BETWEEN 1 AND 3', name=op.f('ck_workspaces_classification')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_workspaces_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.id'], name=op.f('fk_workspaces_scope_id_scopes'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspaces')),
    sa.UniqueConstraint('scope_id', 'name', name='uq_workspaces_scope_name')
    )
    op.create_index(op.f('ix_workspaces_created_by'), 'workspaces', ['created_by'], unique=False)
    op.create_index(op.f('ix_workspaces_scope_id'), 'workspaces', ['scope_id'], unique=False)
    op.create_table('conversations',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('workspace_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('project_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)', name=op.f('ck_conversations_audience_xor')),
    sa.CheckConstraint('project_id IS NULL OR owner_id IS NOT NULL', name=op.f('ck_conversations_project_private')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_conversations_owner_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id', 'owner_id'], ['projects.id', 'projects.owner_id'], name='fk_conversations_project_owner', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_conversations_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index(op.f('ix_conversations_owner_id'), 'conversations', ['owner_id'], unique=False)
    op.create_index(op.f('ix_conversations_project_id'), 'conversations', ['project_id'], unique=False)
    op.create_index(op.f('ix_conversations_workspace_id'), 'conversations', ['workspace_id'], unique=False)
    op.create_table('documents',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('workspace_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('project_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('filename', sa.String(length=500), nullable=False),
    sa.Column('media_type', sa.String(length=200), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)', name=op.f('ck_documents_audience_xor')),
    sa.CheckConstraint('length(content_hash) = 64', name=op.f('ck_documents_sha256_length')),
    sa.CheckConstraint('project_id IS NULL OR owner_id IS NOT NULL', name=op.f('ck_documents_project_private')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_documents_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_documents_owner_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id', 'owner_id'], ['projects.id', 'projects.owner_id'], name='fk_documents_project_owner', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_documents_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_index(op.f('ix_documents_content_hash'), 'documents', ['content_hash'], unique=False)
    op.create_index(op.f('ix_documents_created_by'), 'documents', ['created_by'], unique=False)
    op.create_index(op.f('ix_documents_owner_id'), 'documents', ['owner_id'], unique=False)
    op.create_index(op.f('ix_documents_project_id'), 'documents', ['project_id'], unique=False)
    op.create_index('ix_documents_workspace_created', 'documents', ['workspace_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_documents_workspace_id'), 'documents', ['workspace_id'], unique=False)
    op.create_table('incidents',
    sa.Column('workspace_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('severity', sa.String(length=20), server_default=sa.text("'medium'"), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
    sa.Column('assignee_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('reported_by', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('decision', sa.Text(), server_default=sa.text("''"), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('published', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('escalated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('request_id', sa.String(length=128), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("NOT escalated OR (published AND severity = 'critical')", name=op.f('ck_incidents_escalation_published_critical')),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name=op.f('ck_incidents_severity')),
    sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name=op.f('ck_incidents_status')),
    sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], name=op.f('fk_incidents_assignee_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reported_by'], ['users.id'], name=op.f('fk_incidents_reported_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_incidents_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incidents')),
    sa.UniqueConstraint('workspace_id', 'request_id', name='uq_incidents_workspace_request')
    )
    op.create_index(op.f('ix_incidents_assignee_id'), 'incidents', ['assignee_id'], unique=False)
    op.create_index('ix_incidents_published', 'incidents', ['published', 'escalated'], unique=False)
    op.create_index(op.f('ix_incidents_reported_by'), 'incidents', ['reported_by'], unique=False)
    op.create_index(op.f('ix_incidents_workspace_id'), 'incidents', ['workspace_id'], unique=False)
    op.create_index('ix_incidents_workspace_status_created', 'incidents', ['workspace_id', 'status', 'created_at'], unique=False)
    op.create_table('memberships',
    sa.Column('workspace_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('user_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('role', sa.String(length=20), server_default=sa.text("'member'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('viewer', 'member', 'manager')", name=op.f('ck_memberships_role')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_memberships_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_memberships_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'user_id', name=op.f('pk_memberships'))
    )
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_table('notes',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('project_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('title', sa.String(length=300), server_default=sa.text("''"), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=20), server_default=sa.text("'thought'"), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('observation', 'thought', 'hypothesis', 'decision', 'commitment', 'minutes')", name=op.f('ck_notes_kind')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_notes_owner_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id', 'owner_id'], ['projects.id', 'projects.owner_id'], name='fk_notes_project_owner', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notes')),
    sa.UniqueConstraint('id', 'owner_id', name='uq_notes_id_owner')
    )
    op.create_index(op.f('ix_notes_owner_id'), 'notes', ['owner_id'], unique=False)
    op.create_index('ix_notes_owner_project', 'notes', ['owner_id', 'project_id'], unique=False)
    op.create_index(op.f('ix_notes_project_id'), 'notes', ['project_id'], unique=False)
    op.create_table('scheduled_jobs',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('workspace_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('instructions', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('interval_minutes', sa.Integer(), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
    sa.Column('external_ai_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'paused', 'completed')", name=op.f('ck_scheduled_jobs_status')),
    sa.CheckConstraint('interval_minutes IS NULL OR interval_minutes BETWEEN 15 AND 20160', name=op.f('ck_scheduled_jobs_interval_range')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_scheduled_jobs_owner_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_scheduled_jobs_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scheduled_jobs'))
    )
    op.create_index(op.f('ix_scheduled_jobs_owner_id'), 'scheduled_jobs', ['owner_id'], unique=False)
    op.create_index('ix_scheduled_jobs_status_next_run', 'scheduled_jobs', ['status', 'next_run_at'], unique=False)
    op.create_index(op.f('ix_scheduled_jobs_workspace_id'), 'scheduled_jobs', ['workspace_id'], unique=False)
    op.create_table('document_chunks',
    sa.Column('document_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('ordinal >= 0', name=op.f('ck_document_chunks_ordinal_nonnegative')),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_document_chunks_document_id_documents'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_chunks')),
    sa.UniqueConstraint('document_id', 'ordinal', name='uq_document_chunks_document_ordinal')
    )
    op.create_index(op.f('ix_document_chunks_document_id'), 'document_chunks', ['document_id'], unique=False)
    op.create_table('incident_events',
    sa.Column('incident_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('actor_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_incident_events_actor_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_incident_events_incident_id_incidents'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_events'))
    )
    op.create_index(op.f('ix_incident_events_actor_id'), 'incident_events', ['actor_id'], unique=False)
    op.create_index(op.f('ix_incident_events_incident_id'), 'incident_events', ['incident_id'], unique=False)
    op.create_table('job_dependencies',
    sa.Column('job_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('depends_on_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('job_id <> depends_on_id', name=op.f('ck_job_dependencies_not_self')),
    sa.ForeignKeyConstraint(['depends_on_id'], ['scheduled_jobs.id'], name=op.f('fk_job_dependencies_depends_on_id_scheduled_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['scheduled_jobs.id'], name=op.f('fk_job_dependencies_job_id_scheduled_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('job_id', 'depends_on_id', name=op.f('pk_job_dependencies'))
    )
    op.create_index(op.f('ix_job_dependencies_depends_on_id'), 'job_dependencies', ['depends_on_id'], unique=False)
    op.create_table('job_runs',
    sa.Column('job_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_token', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'blocked')", name=op.f('ck_job_runs_status')),
    sa.CheckConstraint('(lease_until IS NULL) = (lease_token IS NULL)', name=op.f('ck_job_runs_lease_pair')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_job_runs_attempts_nonnegative')),
    sa.ForeignKeyConstraint(['job_id'], ['scheduled_jobs.id'], name=op.f('fk_job_runs_job_id_scheduled_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_runs')),
    sa.UniqueConstraint('job_id', 'scheduled_for', name='uq_job_runs_job_scheduled')
    )
    op.create_index('ix_job_runs_claim', 'job_runs', ['status', 'available_at', 'lease_until'], unique=False)
    op.create_index(op.f('ix_job_runs_job_id'), 'job_runs', ['job_id'], unique=False)
    op.create_table('memories',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('note_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('forgotten_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['note_id', 'owner_id'], ['notes.id', 'notes.owner_id'], name='fk_memories_note_owner', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_memories_owner_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_memories'))
    )
    op.create_index(op.f('ix_memories_note_id'), 'memories', ['note_id'], unique=False)
    op.create_index('ix_memories_owner_forgotten', 'memories', ['owner_id', 'forgotten_at'], unique=False)
    op.create_index(op.f('ix_memories_owner_id'), 'memories', ['owner_id'], unique=False)
    op.create_table('messages',
    sa.Column('conversation_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('author_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('source_refs', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name=op.f('ck_messages_role')),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_messages_author_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    op.create_index(op.f('ix_messages_author_id'), 'messages', ['author_id'], unique=False)
    op.create_index('ix_messages_conversation_created', 'messages', ['conversation_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_table('reminders',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('note_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('open', 'waiting', 'snoozed', 'done', 'cancelled')", name=op.f('ck_reminders_status')),
    sa.ForeignKeyConstraint(['note_id', 'owner_id'], ['notes.id', 'notes.owner_id'], name='fk_reminders_note_owner', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_reminders_owner_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reminders'))
    )
    op.create_index('ix_reminders_due_status', 'reminders', ['due_at', 'status'], unique=False)
    op.create_index(op.f('ix_reminders_note_id'), 'reminders', ['note_id'], unique=False)
    op.create_index(op.f('ix_reminders_owner_id'), 'reminders', ['owner_id'], unique=False)
    op.create_table('reports',
    sa.Column('owner_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('workspace_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('job_run_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('source_refs', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)', name=op.f('ck_reports_audience_xor')),
    sa.ForeignKeyConstraint(['job_run_id'], ['job_runs.id'], name=op.f('fk_reports_job_run_id_job_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_reports_owner_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_reports_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
    sa.UniqueConstraint('job_run_id', name='uq_reports_job_run')
    )
    op.create_index(op.f('ix_reports_owner_id'), 'reports', ['owner_id'], unique=False)
    op.create_index(op.f('ix_reports_workspace_id'), 'reports', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reports_workspace_id'), table_name='reports')
    op.drop_index(op.f('ix_reports_owner_id'), table_name='reports')
    op.drop_table('reports')
    op.drop_index(op.f('ix_reminders_owner_id'), table_name='reminders')
    op.drop_index(op.f('ix_reminders_note_id'), table_name='reminders')
    op.drop_index('ix_reminders_due_status', table_name='reminders')
    op.drop_table('reminders')
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_index('ix_messages_conversation_created', table_name='messages')
    op.drop_index(op.f('ix_messages_author_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_memories_owner_id'), table_name='memories')
    op.drop_index('ix_memories_owner_forgotten', table_name='memories')
    op.drop_index(op.f('ix_memories_note_id'), table_name='memories')
    op.drop_table('memories')
    op.drop_index(op.f('ix_job_runs_job_id'), table_name='job_runs')
    op.drop_index('ix_job_runs_claim', table_name='job_runs')
    op.drop_table('job_runs')
    op.drop_index(op.f('ix_job_dependencies_depends_on_id'), table_name='job_dependencies')
    op.drop_table('job_dependencies')
    op.drop_index(op.f('ix_incident_events_incident_id'), table_name='incident_events')
    op.drop_index(op.f('ix_incident_events_actor_id'), table_name='incident_events')
    op.drop_table('incident_events')
    op.drop_index(op.f('ix_document_chunks_document_id'), table_name='document_chunks')
    op.drop_table('document_chunks')
    op.drop_index(op.f('ix_scheduled_jobs_workspace_id'), table_name='scheduled_jobs')
    op.drop_index('ix_scheduled_jobs_status_next_run', table_name='scheduled_jobs')
    op.drop_index(op.f('ix_scheduled_jobs_owner_id'), table_name='scheduled_jobs')
    op.drop_table('scheduled_jobs')
    op.drop_index(op.f('ix_notes_project_id'), table_name='notes')
    op.drop_index('ix_notes_owner_project', table_name='notes')
    op.drop_index(op.f('ix_notes_owner_id'), table_name='notes')
    op.drop_table('notes')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index('ix_incidents_workspace_status_created', table_name='incidents')
    op.drop_index(op.f('ix_incidents_workspace_id'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_reported_by'), table_name='incidents')
    op.drop_index('ix_incidents_published', table_name='incidents')
    op.drop_index(op.f('ix_incidents_assignee_id'), table_name='incidents')
    op.drop_table('incidents')
    op.drop_index(op.f('ix_documents_workspace_id'), table_name='documents')
    op.drop_index('ix_documents_workspace_created', table_name='documents')
    op.drop_index(op.f('ix_documents_project_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_owner_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_created_by'), table_name='documents')
    op.drop_index(op.f('ix_documents_content_hash'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_conversations_workspace_id'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_project_id'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_owner_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_index(op.f('ix_workspaces_scope_id'), table_name='workspaces')
    op.drop_index(op.f('ix_workspaces_created_by'), table_name='workspaces')
    op.drop_table('workspaces')
    op.drop_index(op.f('ix_subscriptions_scope_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_scope_grants_scope_id'), table_name='scope_grants')
    op.drop_table('scope_grants')
    op.drop_index(op.f('ix_projects_owner_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_index('ix_notifications_user_read_created', table_name='notifications')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_index('ix_auth_sessions_expires_at', table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index('ix_audit_events_resource', table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_actor_id'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('users')
    op.drop_index('uq_scopes_name_parent', table_name='scopes', postgresql_nulls_not_distinct=True)
    op.drop_index(op.f('ix_scopes_parent_id'), table_name='scopes')
    op.drop_table('scopes')
    op.drop_index('ix_login_attempts_window_start', table_name='login_attempts')
    op.drop_table('login_attempts')
    op.drop_table('app_settings')
