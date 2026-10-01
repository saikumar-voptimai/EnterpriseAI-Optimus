-- V-OptimAIse ORM reference DDL. Apply Alembic migrations for installation.
-- Generated from model metadata; no deployment database read.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE app_settings (
	key VARCHAR(200) NOT NULL,
	value JSONB NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_app_settings PRIMARY KEY (id),
	CONSTRAINT uq_app_settings_key UNIQUE (key)
);

CREATE TABLE login_attempts (
	key VARCHAR(320) NOT NULL,
	attempts INTEGER DEFAULT 0 NOT NULL,
	window_start TIMESTAMP WITH TIME ZONE NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_login_attempts PRIMARY KEY (id),
	CONSTRAINT uq_login_attempts_key UNIQUE (key),
	CONSTRAINT ck_login_attempts_nonnegative_attempts CHECK (attempts >= 0)
);

CREATE INDEX ix_login_attempts_window_start ON login_attempts (window_start);

CREATE TABLE scopes (
	name VARCHAR(200) NOT NULL,
	kind VARCHAR(80) NOT NULL,
	parent_id UUID,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_scopes PRIMARY KEY (id),
	CONSTRAINT ck_scopes_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id),
	CONSTRAINT fk_scopes_parent_id_scopes FOREIGN KEY(parent_id) REFERENCES scopes (id) ON DELETE RESTRICT
);

CREATE INDEX ix_scopes_parent_id ON scopes (parent_id);

CREATE UNIQUE INDEX uq_scopes_name_parent ON scopes (parent_id, name) NULLS NOT DISTINCT;

CREATE TABLE users (
	email VARCHAR(320) NOT NULL,
	name VARCHAR(200) NOT NULL,
	password_hash TEXT NOT NULL,
	is_service BOOLEAN DEFAULT false NOT NULL,
	is_admin BOOLEAN DEFAULT false NOT NULL,
	active BOOLEAN DEFAULT true NOT NULL,
	clearance INTEGER DEFAULT 1 NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_users PRIMARY KEY (id),
	CONSTRAINT ck_users_clearance CHECK (clearance BETWEEN 1 AND 3),
	CONSTRAINT ck_users_email_lowercase CHECK (email = lower(email) AND length(email) > 3),
	CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE assistant_settings (
	owner_id UUID NOT NULL,
	reminder_mode VARCHAR(20) DEFAULT 'suggest' NOT NULL,
	memory_mode VARCHAR(20) DEFAULT 'suggest' NOT NULL,
	organization_mode VARCHAR(20) DEFAULT 'suggest' NOT NULL,
	daily_checkin_enabled BOOLEAN DEFAULT false NOT NULL,
	daily_checkin_time VARCHAR(5) DEFAULT '17:00' NOT NULL,
	reminder_hour INTEGER DEFAULT 13 NOT NULL,
	next_checkin_at TIMESTAMP WITH TIME ZONE,
	next_organization_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_assistant_settings PRIMARY KEY (owner_id),
	CONSTRAINT ck_assistant_settings_reminder_mode CHECK (reminder_mode IN ('off','suggest','automatic')),
	CONSTRAINT ck_assistant_settings_memory_mode CHECK (memory_mode IN ('off','suggest','automatic')),
	CONSTRAINT ck_assistant_settings_organization_mode CHECK (organization_mode IN ('off','suggest','automatic')),
	CONSTRAINT ck_assistant_settings_reminder_hour CHECK (reminder_hour BETWEEN 0 AND 23),
	CONSTRAINT fk_assistant_settings_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_assistant_settings_daily_due ON assistant_settings (next_checkin_at);

CREATE INDEX ix_assistant_settings_organization_due ON assistant_settings (next_organization_at);

CREATE TABLE audit_events (
	actor_id UUID,
	action VARCHAR(100) NOT NULL,
	resource_type VARCHAR(80) NOT NULL,
	resource_id UUID,
	details JSONB DEFAULT '{}'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_audit_events PRIMARY KEY (id),
	CONSTRAINT fk_audit_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_id);

CREATE INDEX ix_audit_events_resource ON audit_events (resource_type, resource_id);

CREATE TABLE auth_sessions (
	user_id UUID NOT NULL,
	token_hash VARCHAR(128) NOT NULL,
	csrf_token VARCHAR(128) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_auth_sessions PRIMARY KEY (id),
	CONSTRAINT uq_auth_sessions_token_hash UNIQUE (token_hash),
	CONSTRAINT fk_auth_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE TABLE meeting_rooms (
	owner_id UUID NOT NULL,
	title VARCHAR(300) NOT NULL,
	starts_at TIMESTAMP WITH TIME ZONE,
	ends_at TIMESTAMP WITH TIME ZONE,
	status VARCHAR(30) NOT NULL,
	revision INTEGER NOT NULL,
	minutes JSONB DEFAULT '{}'::jsonb NOT NULL,
	workspace_ids JSONB DEFAULT '[]'::jsonb NOT NULL,
	reviewed_at TIMESTAMP WITH TIME ZONE,
	published_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_meeting_rooms PRIMARY KEY (id),
	CONSTRAINT ck_meeting_rooms_status CHECK (status IN ('open','draft','reviewed','published','archived')),
	CONSTRAINT fk_meeting_rooms_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_meeting_rooms_owner_id ON meeting_rooms (owner_id);

CREATE TABLE notifications (
	user_id UUID NOT NULL,
	kind VARCHAR(80) NOT NULL,
	title VARCHAR(300) NOT NULL,
	body TEXT NOT NULL,
	resource_type VARCHAR(80) NOT NULL,
	resource_id UUID,
	read_at TIMESTAMP WITH TIME ZONE,
	dedupe_key VARCHAR(500),
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_notifications PRIMARY KEY (id),
	CONSTRAINT uq_notifications_dedupe_key UNIQUE (dedupe_key),
	CONSTRAINT fk_notifications_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE INDEX ix_notifications_user_read_created ON notifications (user_id, read_at, created_at);

CREATE TABLE personal_actions (
	owner_id UUID NOT NULL,
	request_id VARCHAR(180) NOT NULL,
	kind VARCHAR(80) NOT NULL,
	status VARCHAR(20) DEFAULT 'applied' NOT NULL,
	resource_id UUID,
	payload JSONB DEFAULT '{}'::jsonb NOT NULL,
	undo_until TIMESTAMP WITH TIME ZONE NOT NULL,
	undone_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_personal_actions PRIMARY KEY (id),
	CONSTRAINT uq_personal_actions_owner_request UNIQUE (owner_id, request_id),
	CONSTRAINT ck_personal_actions_status CHECK (status IN ('pending','applied','undone','cancelled','failed')),
	CONSTRAINT fk_personal_actions_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_personal_actions_due ON personal_actions (status, undo_until);

CREATE INDEX ix_personal_actions_owner_created ON personal_actions (owner_id, created_at);

CREATE INDEX ix_personal_actions_owner_id ON personal_actions (owner_id);

CREATE TABLE projects (
	owner_id UUID NOT NULL,
	name VARCHAR(200) NOT NULL,
	goal TEXT DEFAULT '' NOT NULL,
	archived BOOLEAN DEFAULT false NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_projects PRIMARY KEY (id),
	CONSTRAINT uq_projects_id_owner UNIQUE (id, owner_id),
	CONSTRAINT fk_projects_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_projects_owner_id ON projects (owner_id);

CREATE TABLE scope_grants (
	user_id UUID NOT NULL,
	scope_id UUID NOT NULL,
	can_review BOOLEAN DEFAULT false NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_scope_grants PRIMARY KEY (user_id, scope_id),
	CONSTRAINT fk_scope_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_scope_grants_scope_id_scopes FOREIGN KEY(scope_id) REFERENCES scopes (id) ON DELETE CASCADE
);

CREATE INDEX ix_scope_grants_scope_id ON scope_grants (scope_id);

CREATE TABLE scope_role_assignments (
	user_id UUID NOT NULL,
	scope_id UUID NOT NULL,
	manage_workspaces BOOLEAN DEFAULT true NOT NULL,
	manage_people BOOLEAN DEFAULT false NOT NULL,
	effective_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	effective_until TIMESTAMP WITH TIME ZONE,
	assigned_by UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_scope_role_assignments PRIMARY KEY (id),
	CONSTRAINT uq_scope_role_assignments_user_scope UNIQUE (user_id, scope_id),
	CONSTRAINT fk_scope_role_assignments_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_scope_role_assignments_scope_id_scopes FOREIGN KEY(scope_id) REFERENCES scopes (id) ON DELETE CASCADE,
	CONSTRAINT fk_scope_role_assignments_assigned_by_users FOREIGN KEY(assigned_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_scope_role_assignments_scope_id ON scope_role_assignments (scope_id);

CREATE INDEX ix_scope_role_assignments_user_id ON scope_role_assignments (user_id);

CREATE TABLE subscriptions (
	user_id UUID NOT NULL,
	scope_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_subscriptions PRIMARY KEY (user_id, scope_id),
	CONSTRAINT fk_subscriptions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_subscriptions_scope_id_scopes FOREIGN KEY(scope_id) REFERENCES scopes (id) ON DELETE CASCADE
);

CREATE INDEX ix_subscriptions_scope_id ON subscriptions (scope_id);

CREATE TABLE user_preferences (
	user_id UUID NOT NULL,
	language VARCHAR(80) NOT NULL,
	timezone VARCHAR(80) NOT NULL,
	units VARCHAR(40) NOT NULL,
	response_style VARCHAR(80) NOT NULL,
	personal_instructions TEXT NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_user_preferences PRIMARY KEY (id),
	CONSTRAINT uq_user_preferences_user UNIQUE (user_id),
	CONSTRAINT fk_user_preferences_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_user_preferences_user_id ON user_preferences (user_id);

CREATE TABLE workspaces (
	name VARCHAR(200) NOT NULL,
	description TEXT DEFAULT '' NOT NULL,
	scope_id UUID NOT NULL,
	classification INTEGER DEFAULT 1 NOT NULL,
	external_ai_enabled BOOLEAN DEFAULT false NOT NULL,
	created_by UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_workspaces PRIMARY KEY (id),
	CONSTRAINT ck_workspaces_classification CHECK (classification BETWEEN 1 AND 3),
	CONSTRAINT uq_workspaces_scope_name UNIQUE (scope_id, name),
	CONSTRAINT fk_workspaces_scope_id_scopes FOREIGN KEY(scope_id) REFERENCES scopes (id) ON DELETE RESTRICT,
	CONSTRAINT fk_workspaces_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_workspaces_created_by ON workspaces (created_by);

CREATE INDEX ix_workspaces_scope_id ON workspaces (scope_id);

CREATE TABLE connections (
	owner_id UUID NOT NULL,
	workspace_id UUID,
	name VARCHAR(200) NOT NULL,
	provider VARCHAR(40) NOT NULL,
	config JSONB DEFAULT '{}'::jsonb NOT NULL,
	encrypted_credentials TEXT DEFAULT '' NOT NULL,
	status VARCHAR(30) DEFAULT 'pending' NOT NULL,
	last_error VARCHAR(500),
	last_synced_at TIMESTAMP WITH TIME ZONE,
	next_sync_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_connections PRIMARY KEY (id),
	CONSTRAINT ck_connections_provider CHECK (provider IN ('microsoft','zoom','influxdb','teams_workflow')),
	CONSTRAINT ck_connections_status CHECK (status IN ('pending','connected','error','disconnected')),
	CONSTRAINT fk_connections_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_connections_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);

CREATE INDEX ix_connections_owner_id ON connections (owner_id);

CREATE INDEX ix_connections_sync ON connections (provider, status, next_sync_at);

CREATE INDEX ix_connections_workspace_id ON connections (workspace_id);

CREATE TABLE conversations (
	owner_id UUID,
	workspace_id UUID,
	project_id UUID,
	title VARCHAR(300) NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_conversations PRIMARY KEY (id),
	CONSTRAINT ck_conversations_audience_xor CHECK ((owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)),
	CONSTRAINT ck_conversations_project_private CHECK (project_id IS NULL OR owner_id IS NOT NULL),
	CONSTRAINT fk_conversations_project_owner FOREIGN KEY(project_id, owner_id) REFERENCES projects (id, owner_id) ON DELETE RESTRICT,
	CONSTRAINT fk_conversations_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_conversations_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);

CREATE INDEX ix_conversations_owner_id ON conversations (owner_id);

CREATE INDEX ix_conversations_project_id ON conversations (project_id);

CREATE INDEX ix_conversations_workspace_id ON conversations (workspace_id);

CREATE TABLE documents (
	owner_id UUID,
	workspace_id UUID,
	project_id UUID,
	title VARCHAR(300) NOT NULL,
	filename VARCHAR(500) NOT NULL,
	media_type VARCHAR(200) NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	body TEXT NOT NULL,
	created_by UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_documents PRIMARY KEY (id),
	CONSTRAINT ck_documents_audience_xor CHECK ((owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)),
	CONSTRAINT ck_documents_project_private CHECK (project_id IS NULL OR owner_id IS NOT NULL),
	CONSTRAINT fk_documents_project_owner FOREIGN KEY(project_id, owner_id) REFERENCES projects (id, owner_id) ON DELETE RESTRICT,
	CONSTRAINT ck_documents_sha256_length CHECK (length(content_hash) = 64),
	CONSTRAINT fk_documents_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_documents_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_documents_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_documents_content_hash ON documents (content_hash);

CREATE INDEX ix_documents_created_by ON documents (created_by);

CREATE INDEX ix_documents_owner_id ON documents (owner_id);

CREATE INDEX ix_documents_project_id ON documents (project_id);

CREATE INDEX ix_documents_workspace_created ON documents (workspace_id, created_at);

CREATE INDEX ix_documents_workspace_id ON documents (workspace_id);

CREATE TABLE incidents (
	workspace_id UUID NOT NULL,
	title VARCHAR(300) NOT NULL,
	summary TEXT NOT NULL,
	severity VARCHAR(20) DEFAULT 'medium' NOT NULL,
	status VARCHAR(20) DEFAULT 'open' NOT NULL,
	assignee_id UUID,
	reported_by UUID NOT NULL,
	decision TEXT DEFAULT '' NOT NULL,
	resolution TEXT,
	published BOOLEAN DEFAULT false NOT NULL,
	escalated BOOLEAN DEFAULT false NOT NULL,
	request_id VARCHAR(128) NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_incidents PRIMARY KEY (id),
	CONSTRAINT ck_incidents_severity CHECK (severity IN ('low', 'medium', 'high', 'critical')),
	CONSTRAINT ck_incidents_status CHECK (status IN ('open', 'acknowledged', 'resolved')),
	CONSTRAINT ck_incidents_escalation_published_critical CHECK (NOT escalated OR (published AND severity = 'critical')),
	CONSTRAINT uq_incidents_workspace_request UNIQUE (workspace_id, request_id),
	CONSTRAINT fk_incidents_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_incidents_assignee_id_users FOREIGN KEY(assignee_id) REFERENCES users (id) ON DELETE SET NULL,
	CONSTRAINT fk_incidents_reported_by_users FOREIGN KEY(reported_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_incidents_assignee_id ON incidents (assignee_id);

CREATE INDEX ix_incidents_published ON incidents (published, escalated);

CREATE INDEX ix_incidents_reported_by ON incidents (reported_by);

CREATE INDEX ix_incidents_workspace_id ON incidents (workspace_id);

CREATE INDEX ix_incidents_workspace_status_created ON incidents (workspace_id, status, created_at);

CREATE TABLE meeting_artifacts (
	room_id UUID NOT NULL,
	kind VARCHAR(30) NOT NULL,
	content TEXT NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	source_label VARCHAR(300) NOT NULL,
	provider_reference JSONB DEFAULT '{}'::jsonb NOT NULL,
	imported_by UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_meeting_artifacts PRIMARY KEY (id),
	CONSTRAINT uq_meeting_artifacts_room_id UNIQUE (room_id, content_hash),
	CONSTRAINT ck_meeting_artifacts_kind CHECK (kind IN ('transcript','minutes','notes')),
	CONSTRAINT fk_meeting_artifacts_room_id_meeting_rooms FOREIGN KEY(room_id) REFERENCES meeting_rooms (id) ON DELETE CASCADE,
	CONSTRAINT fk_meeting_artifacts_imported_by_users FOREIGN KEY(imported_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_meeting_artifacts_room_id ON meeting_artifacts (room_id);

CREATE TABLE meeting_attendees (
	room_id UUID NOT NULL,
	user_id UUID NOT NULL,
	accepted_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_meeting_attendees PRIMARY KEY (room_id, user_id),
	CONSTRAINT fk_meeting_attendees_room_id_meeting_rooms FOREIGN KEY(room_id) REFERENCES meeting_rooms (id) ON DELETE CASCADE,
	CONSTRAINT fk_meeting_attendees_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE meeting_distributions (
	room_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	audience_key VARCHAR(100) NOT NULL,
	resource_type VARCHAR(30) NOT NULL,
	resource_id UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_meeting_distributions PRIMARY KEY (id),
	CONSTRAINT uq_meeting_distributions_room_id UNIQUE (room_id, revision, audience_key),
	CONSTRAINT fk_meeting_distributions_room_id_meeting_rooms FOREIGN KEY(room_id) REFERENCES meeting_rooms (id) ON DELETE CASCADE
);

CREATE INDEX ix_meeting_distributions_room_id ON meeting_distributions (room_id);

CREATE TABLE meeting_extractions (
	room_id UUID NOT NULL,
	requested_by UUID NOT NULL,
	source_revision INTEGER NOT NULL,
	artifact_manifest JSONB DEFAULT '[]'::jsonb NOT NULL,
	chunks JSONB DEFAULT '[]'::jsonb NOT NULL,
	cursor INTEGER NOT NULL,
	partials JSONB DEFAULT '[]'::jsonb NOT NULL,
	summary_work JSONB DEFAULT '[]'::jsonb NOT NULL,
	phase VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	attempts INTEGER NOT NULL,
	available_at TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	lease_token UUID,
	error VARCHAR(500),
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_meeting_extractions PRIMARY KEY (id),
	CONSTRAINT ck_meeting_extractions_status CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
	CONSTRAINT ck_meeting_extractions_phase CHECK (phase IN ('map','reduce','finalize')),
	CONSTRAINT ck_meeting_extractions_lease_pair CHECK ((lease_until IS NULL) = (lease_token IS NULL)),
	CONSTRAINT fk_meeting_extractions_room_id_meeting_rooms FOREIGN KEY(room_id) REFERENCES meeting_rooms (id) ON DELETE CASCADE,
	CONSTRAINT fk_meeting_extractions_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_meeting_extractions_claim ON meeting_extractions (status, available_at, lease_until);

CREATE INDEX ix_meeting_extractions_room_id ON meeting_extractions (room_id);

CREATE TABLE memberships (
	workspace_id UUID NOT NULL,
	user_id UUID NOT NULL,
	role VARCHAR(20) DEFAULT 'member' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_memberships PRIMARY KEY (workspace_id, user_id),
	CONSTRAINT ck_memberships_role CHECK (role IN ('viewer', 'member', 'manager')),
	CONSTRAINT fk_memberships_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_memberships_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_memberships_user_id ON memberships (user_id);

CREATE TABLE notes (
	owner_id UUID NOT NULL,
	project_id UUID,
	title VARCHAR(300) DEFAULT '' NOT NULL,
	body TEXT NOT NULL,
	kind VARCHAR(20) DEFAULT 'thought' NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_notes PRIMARY KEY (id),
	CONSTRAINT fk_notes_project_owner FOREIGN KEY(project_id, owner_id) REFERENCES projects (id, owner_id) ON DELETE RESTRICT,
	CONSTRAINT uq_notes_id_owner UNIQUE (id, owner_id),
	CONSTRAINT ck_notes_kind CHECK (kind IN ('observation', 'thought', 'hypothesis', 'decision', 'commitment', 'minutes')),
	CONSTRAINT fk_notes_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_notes_owner_id ON notes (owner_id);

CREATE INDEX ix_notes_owner_project ON notes (owner_id, project_id);

CREATE INDEX ix_notes_project_id ON notes (project_id);

CREATE TABLE organization_changes (
	owner_id UUID NOT NULL,
	status VARCHAR(20) DEFAULT 'preview' NOT NULL,
	moves JSONB DEFAULT '[]'::jsonb NOT NULL,
	action_id UUID,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_organization_changes PRIMARY KEY (id),
	CONSTRAINT ck_organization_changes_status CHECK (status IN ('preview','applied','undone')),
	CONSTRAINT fk_organization_changes_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_organization_changes_action_id_personal_actions FOREIGN KEY(action_id) REFERENCES personal_actions (id) ON DELETE SET NULL
);

CREATE INDEX ix_organization_changes_owner_id ON organization_changes (owner_id);

CREATE TABLE project_folders (
	owner_id UUID NOT NULL,
	project_id UUID NOT NULL,
	parent_id UUID,
	name VARCHAR(120) NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_project_folders PRIMARY KEY (id),
	CONSTRAINT uq_project_folders_id_owner_project UNIQUE (id, owner_id, project_id),
	CONSTRAINT fk_project_folders_project_owner FOREIGN KEY(project_id, owner_id) REFERENCES projects (id, owner_id) ON DELETE CASCADE,
	CONSTRAINT fk_project_folders_parent_scope FOREIGN KEY(parent_id, owner_id, project_id) REFERENCES project_folders (id, owner_id, project_id) ON DELETE RESTRICT,
	CONSTRAINT ck_project_folders_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id),
	CONSTRAINT fk_project_folders_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_project_folders_owner_id ON project_folders (owner_id);

CREATE INDEX ix_project_folders_project_id ON project_folders (project_id);

CREATE UNIQUE INDEX uq_project_folders_sibling_name ON project_folders (project_id, parent_id, name) NULLS NOT DISTINCT;

CREATE TABLE scheduled_jobs (
	owner_id UUID NOT NULL,
	workspace_id UUID,
	name VARCHAR(200) NOT NULL,
	instructions TEXT NOT NULL,
	model VARCHAR(200) NOT NULL,
	interval_minutes INTEGER,
	next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(20) DEFAULT 'active' NOT NULL,
	external_ai_enabled BOOLEAN DEFAULT false NOT NULL,
	execution_user_id UUID,
	task_type VARCHAR(40) DEFAULT 'analysis' NOT NULL,
	config JSONB DEFAULT '{}'::jsonb NOT NULL,
	timezone VARCHAR(80) DEFAULT 'UTC' NOT NULL,
	approval_status VARCHAR(20) DEFAULT 'legacy' NOT NULL,
	revision INTEGER DEFAULT 1 NOT NULL,
	approved_revision INTEGER,
	activation_not_before TIMESTAMP WITH TIME ZONE,
	dependency_policy VARCHAR(20) DEFAULT 'pass_warn' NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_scheduled_jobs PRIMARY KEY (id),
	CONSTRAINT ck_scheduled_jobs_interval_range CHECK (interval_minutes IS NULL OR interval_minutes BETWEEN 15 AND 20160),
	CONSTRAINT ck_scheduled_jobs_status CHECK (status IN ('active', 'paused', 'completed')),
	CONSTRAINT ck_scheduled_jobs_approval_status CHECK (approval_status IN ('legacy','pending','approved','rejected')),
	CONSTRAINT ck_scheduled_jobs_task_type CHECK (task_type IN ('analysis','validation','handover','production_comparison','morning_brief')),
	CONSTRAINT ck_scheduled_jobs_dependency_policy CHECK (dependency_policy IN ('pass','pass_warn','any')),
	CONSTRAINT fk_scheduled_jobs_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_scheduled_jobs_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_scheduled_jobs_execution_user_id_users FOREIGN KEY(execution_user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_scheduled_jobs_owner_id ON scheduled_jobs (owner_id);

CREATE INDEX ix_scheduled_jobs_status_next_run ON scheduled_jobs (status, next_run_at);

CREATE INDEX ix_scheduled_jobs_workspace_id ON scheduled_jobs (workspace_id);

CREATE TABLE workspace_automations (
	workspace_id UUID NOT NULL,
	service_user_id UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_workspace_automations PRIMARY KEY (id),
	CONSTRAINT uq_workspace_automations_workspace_id UNIQUE (workspace_id),
	CONSTRAINT fk_workspace_automations_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT uq_workspace_automations_service_user_id UNIQUE (service_user_id),
	CONSTRAINT fk_workspace_automations_service_user_id_users FOREIGN KEY(service_user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE TABLE workspace_briefs (
	workspace_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	body TEXT NOT NULL,
	details JSONB NOT NULL,
	created_by UUID NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_workspace_briefs PRIMARY KEY (id),
	CONSTRAINT uq_workspace_briefs_revision UNIQUE (workspace_id, revision),
	CONSTRAINT ck_workspace_briefs_status CHECK (status IN ('draft', 'published', 'superseded')),
	CONSTRAINT fk_workspace_briefs_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_workspace_briefs_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_workspace_briefs_workspace_id ON workspace_briefs (workspace_id);

CREATE UNIQUE INDEX uq_workspace_briefs_published ON workspace_briefs (workspace_id) WHERE status = 'published';

CREATE TABLE calendar_events (
	connection_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	calendar_id VARCHAR(1024) NOT NULL,
	provider_id VARCHAR(1024) NOT NULL,
	title VARCHAR(500) NOT NULL,
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
	cancelled BOOLEAN NOT NULL,
	busy BOOLEAN NOT NULL,
	details JSONB DEFAULT '{}'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_calendar_events PRIMARY KEY (id),
	CONSTRAINT uq_calendar_events_connection_id UNIQUE (connection_id, calendar_id, provider_id),
	CONSTRAINT fk_calendar_events_connection_id_connections FOREIGN KEY(connection_id) REFERENCES connections (id) ON DELETE CASCADE,
	CONSTRAINT fk_calendar_events_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_calendar_events_owner_id ON calendar_events (owner_id);

CREATE INDEX ix_calendar_events_window ON calendar_events (owner_id, starts_at, ends_at);

CREATE TABLE deliveries (
	owner_id UUID NOT NULL,
	connection_id UUID,
	channel VARCHAR(20) NOT NULL,
	recipient VARCHAR(320) NOT NULL,
	subject VARCHAR(300) NOT NULL,
	body TEXT NOT NULL,
	source_refs JSONB DEFAULT '[]'::jsonb NOT NULL,
	request_id VARCHAR(128) NOT NULL,
	status VARCHAR(20) NOT NULL,
	available_at TIMESTAMP WITH TIME ZONE NOT NULL,
	undo_until TIMESTAMP WITH TIME ZONE NOT NULL,
	attempts INTEGER NOT NULL,
	last_error VARCHAR(500),
	sent_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_deliveries PRIMARY KEY (id),
	CONSTRAINT uq_deliveries_owner_id UNIQUE (owner_id, request_id),
	CONSTRAINT ck_deliveries_channel CHECK (channel IN ('email','teams')),
	CONSTRAINT ck_deliveries_status CHECK (status IN ('pending','sending','sent','cancelled','failed','unknown')),
	CONSTRAINT fk_deliveries_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_deliveries_connection_id_connections FOREIGN KEY(connection_id) REFERENCES connections (id) ON DELETE SET NULL
);

CREATE INDEX ix_deliveries_due ON deliveries (status, available_at);

CREATE INDEX ix_deliveries_owner_id ON deliveries (owner_id);

CREATE TABLE document_chunks (
	document_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	body TEXT NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_document_chunks PRIMARY KEY (id),
	CONSTRAINT uq_document_chunks_document_ordinal UNIQUE (document_id, ordinal),
	CONSTRAINT ck_document_chunks_ordinal_nonnegative CHECK (ordinal >= 0),
	CONSTRAINT fk_document_chunks_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);

CREATE INDEX ix_document_chunks_document_id ON document_chunks (document_id);

CREATE TABLE document_revisions (
	document_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	is_current BOOLEAN NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	original_bytes BYTEA,
	original_size INTEGER NOT NULL,
	filename VARCHAR(500) NOT NULL,
	media_type VARCHAR(200) NOT NULL,
	extracted_text TEXT NOT NULL,
	index_status VARCHAR(24) NOT NULL,
	embedding_model VARCHAR(200),
	embedding_dimensions INTEGER,
	chunk_count INTEGER NOT NULL,
	indexed_count INTEGER NOT NULL,
	attempts INTEGER NOT NULL,
	error TEXT,
	retry_at TIMESTAMP WITH TIME ZONE,
	lease_until TIMESTAMP WITH TIME ZONE,
	lease_token UUID,
	created_by UUID NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_document_revisions PRIMARY KEY (id),
	CONSTRAINT uq_document_revisions_revision UNIQUE (document_id, revision),
	CONSTRAINT ck_document_revisions_index_status CHECK (index_status IN ('lexical_ready', 'vector_pending', 'indexing', 'ready', 'failed', 'disabled')),
	CONSTRAINT fk_document_revisions_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_revisions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_document_revisions_document_id ON document_revisions (document_id);

CREATE INDEX ix_document_revisions_index_queue ON document_revisions (index_status, retry_at, lease_until);

CREATE UNIQUE INDEX uq_document_revisions_current ON document_revisions (document_id) WHERE is_current;

CREATE TABLE incident_events (
	incident_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	action VARCHAR(80) NOT NULL,
	detail JSONB DEFAULT '{}'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_incident_events PRIMARY KEY (id),
	CONSTRAINT fk_incident_events_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE,
	CONSTRAINT fk_incident_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_incident_events_actor_id ON incident_events (actor_id);

CREATE INDEX ix_incident_events_incident_id ON incident_events (incident_id);

CREATE TABLE job_dependencies (
	job_id UUID NOT NULL,
	depends_on_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_job_dependencies PRIMARY KEY (job_id, depends_on_id),
	CONSTRAINT ck_job_dependencies_not_self CHECK (job_id <> depends_on_id),
	CONSTRAINT fk_job_dependencies_job_id_scheduled_jobs FOREIGN KEY(job_id) REFERENCES scheduled_jobs (id) ON DELETE CASCADE,
	CONSTRAINT fk_job_dependencies_depends_on_id_scheduled_jobs FOREIGN KEY(depends_on_id) REFERENCES scheduled_jobs (id) ON DELETE RESTRICT
);

CREATE INDEX ix_job_dependencies_depends_on_id ON job_dependencies (depends_on_id);

CREATE TABLE job_revisions (
	job_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	snapshot JSONB NOT NULL,
	digest VARCHAR(64) NOT NULL,
	submitted_by UUID NOT NULL,
	decision VARCHAR(20) DEFAULT 'pending' NOT NULL,
	reviewed_by UUID,
	reviewed_at TIMESTAMP WITH TIME ZONE,
	comment TEXT DEFAULT '' NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_job_revisions PRIMARY KEY (id),
	CONSTRAINT uq_job_revisions_job_revision UNIQUE (job_id, revision),
	CONSTRAINT fk_job_revisions_job_id_scheduled_jobs FOREIGN KEY(job_id) REFERENCES scheduled_jobs (id) ON DELETE CASCADE,
	CONSTRAINT fk_job_revisions_submitted_by_users FOREIGN KEY(submitted_by) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT fk_job_revisions_reviewed_by_users FOREIGN KEY(reviewed_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_job_revisions_job_id ON job_revisions (job_id);

CREATE TABLE job_runs (
	job_id UUID NOT NULL,
	trigger_kind VARCHAR(20) DEFAULT 'scheduled' NOT NULL,
	scheduled_for TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(20) DEFAULT 'queued' NOT NULL,
	attempts INTEGER DEFAULT 0 NOT NULL,
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	lease_token UUID,
	error TEXT,
	result TEXT,
	outcome JSONB DEFAULT '{}'::jsonb NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_job_runs PRIMARY KEY (id),
	CONSTRAINT uq_job_runs_job_scheduled UNIQUE (job_id, scheduled_for),
	CONSTRAINT ck_job_runs_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'blocked')),
	CONSTRAINT ck_job_runs_trigger_kind CHECK (trigger_kind IN ('scheduled','manual')),
	CONSTRAINT ck_job_runs_attempts_nonnegative CHECK (attempts >= 0),
	CONSTRAINT ck_job_runs_lease_pair CHECK ((lease_until IS NULL) = (lease_token IS NULL)),
	CONSTRAINT fk_job_runs_job_id_scheduled_jobs FOREIGN KEY(job_id) REFERENCES scheduled_jobs (id) ON DELETE CASCADE
);

CREATE INDEX ix_job_runs_claim ON job_runs (status, available_at, lease_until);

CREATE INDEX ix_job_runs_job_id ON job_runs (job_id);

CREATE TABLE memories (
	owner_id UUID NOT NULL,
	note_id UUID NOT NULL,
	text TEXT NOT NULL,
	forgotten_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_memories PRIMARY KEY (id),
	CONSTRAINT fk_memories_note_owner FOREIGN KEY(note_id, owner_id) REFERENCES notes (id, owner_id) ON DELETE CASCADE,
	CONSTRAINT fk_memories_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_memories_note_id ON memories (note_id);

CREATE INDEX ix_memories_owner_forgotten ON memories (owner_id, forgotten_at);

CREATE INDEX ix_memories_owner_id ON memories (owner_id);

CREATE TABLE messages (
	conversation_id UUID NOT NULL,
	author_id UUID,
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	model VARCHAR(200),
	usage JSONB DEFAULT '{}'::jsonb NOT NULL,
	source_refs JSONB DEFAULT '[]'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_messages PRIMARY KEY (id),
	CONSTRAINT ck_messages_role CHECK (role IN ('user', 'assistant', 'system')),
	CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
	CONSTRAINT fk_messages_author_id_users FOREIGN KEY(author_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_messages_author_id ON messages (author_id);

CREATE INDEX ix_messages_conversation_created ON messages (conversation_id, created_at);

CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

CREATE TABLE note_placements (
	note_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	project_id UUID NOT NULL,
	folder_id UUID NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_note_placements PRIMARY KEY (note_id),
	CONSTRAINT fk_note_placements_note_owner FOREIGN KEY(note_id, owner_id) REFERENCES notes (id, owner_id) ON DELETE CASCADE,
	CONSTRAINT fk_note_placements_folder_scope FOREIGN KEY(folder_id, owner_id, project_id) REFERENCES project_folders (id, owner_id, project_id) ON DELETE CASCADE,
	CONSTRAINT fk_note_placements_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_note_placements_folder_id ON note_placements (folder_id);

CREATE INDEX ix_note_placements_owner_id ON note_placements (owner_id);

CREATE TABLE oauth_attempts (
	connection_id UUID NOT NULL,
	state_hash VARCHAR(64) NOT NULL,
	encrypted_verifier TEXT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	used_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_oauth_attempts PRIMARY KEY (id),
	CONSTRAINT uq_oauth_attempts_state_hash UNIQUE (state_hash),
	CONSTRAINT fk_oauth_attempts_connection_id_connections FOREIGN KEY(connection_id) REFERENCES connections (id) ON DELETE CASCADE
);

CREATE INDEX ix_oauth_attempts_connection_id ON oauth_attempts (connection_id);

CREATE INDEX ix_oauth_attempts_expiry ON oauth_attempts (expires_at);

CREATE TABLE personal_captures (
	owner_id UUID NOT NULL,
	request_id VARCHAR(128) NOT NULL,
	text TEXT NOT NULL,
	timezone VARCHAR(100) NOT NULL,
	data_date DATE NOT NULL,
	note_id UUID,
	status VARCHAR(30) DEFAULT 'saved' NOT NULL,
	classification JSONB DEFAULT '{}'::jsonb NOT NULL,
	classification_error VARCHAR(500),
	classification_model VARCHAR(200),
	explicit_due_at TIMESTAMP WITH TIME ZONE,
	classification_token VARCHAR(36),
	classification_lease_until TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_personal_captures PRIMARY KEY (id),
	CONSTRAINT uq_personal_captures_owner_request UNIQUE (owner_id, request_id),
	CONSTRAINT uq_personal_captures_id_owner UNIQUE (id, owner_id),
	CONSTRAINT ck_personal_captures_status CHECK (status IN ('saved','classifying','classified','classification_failed','applied','undone')),
	CONSTRAINT fk_personal_captures_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_personal_captures_note_id_notes FOREIGN KEY(note_id) REFERENCES notes (id) ON DELETE SET NULL
);

CREATE INDEX ix_personal_captures_note_id ON personal_captures (note_id);

CREATE INDEX ix_personal_captures_owner_created ON personal_captures (owner_id, created_at);

CREATE INDEX ix_personal_captures_owner_id ON personal_captures (owner_id);

CREATE TABLE reminders (
	owner_id UUID NOT NULL,
	note_id UUID NOT NULL,
	title VARCHAR(300) NOT NULL,
	due_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(20) DEFAULT 'open' NOT NULL,
	notified_at TIMESTAMP WITH TIME ZONE,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_reminders PRIMARY KEY (id),
	CONSTRAINT fk_reminders_note_owner FOREIGN KEY(note_id, owner_id) REFERENCES notes (id, owner_id) ON DELETE CASCADE,
	CONSTRAINT ck_reminders_status CHECK (status IN ('open', 'waiting', 'snoozed', 'done', 'cancelled')),
	CONSTRAINT fk_reminders_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_reminders_due_status ON reminders (due_at, status);

CREATE INDEX ix_reminders_note_id ON reminders (note_id);

CREATE INDEX ix_reminders_owner_id ON reminders (owner_id);

CREATE TABLE agent_runs (
	owner_id UUID NOT NULL,
	workspace_id UUID,
	conversation_id UUID,
	project_id UUID,
	user_message_id UUID,
	assistant_message_id UUID,
	request_key VARCHAR(128) NOT NULL,
	kind VARCHAR(40) DEFAULT 'chat' NOT NULL,
	model VARCHAR(200) NOT NULL,
	status VARCHAR(20) DEFAULT 'queued' NOT NULL,
	stage VARCHAR(60) DEFAULT 'queued' NOT NULL,
	attempts INTEGER DEFAULT 0 NOT NULL,
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	lease_token UUID,
	lease_until TIMESTAMP WITH TIME ZONE,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	error TEXT,
	result TEXT,
	source_refs JSONB DEFAULT '[]'::jsonb NOT NULL,
	context_metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	usage JSONB DEFAULT '{}'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_agent_runs PRIMARY KEY (id),
	CONSTRAINT ck_agent_runs_status CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
	CONSTRAINT ck_agent_runs_lease_pair CHECK ((lease_token IS NULL) = (lease_until IS NULL)),
	CONSTRAINT uq_agent_runs_owner_request UNIQUE (owner_id, request_key),
	CONSTRAINT fk_agent_runs_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_agent_runs_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_agent_runs_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
	CONSTRAINT fk_agent_runs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL,
	CONSTRAINT fk_agent_runs_user_message_id_messages FOREIGN KEY(user_message_id) REFERENCES messages (id) ON DELETE SET NULL,
	CONSTRAINT fk_agent_runs_assistant_message_id_messages FOREIGN KEY(assistant_message_id) REFERENCES messages (id) ON DELETE SET NULL
);

CREATE INDEX ix_agent_runs_claim ON agent_runs (status, available_at, lease_until);

CREATE INDEX ix_agent_runs_conversation_id ON agent_runs (conversation_id);

CREATE INDEX ix_agent_runs_owner_id ON agent_runs (owner_id);

CREATE INDEX ix_agent_runs_workspace_id ON agent_runs (workspace_id);

CREATE UNIQUE INDEX uq_agent_runs_active_conversation ON agent_runs (conversation_id) WHERE conversation_id IS NOT NULL AND status IN ('queued','running');

CREATE TABLE conversation_summaries (
	conversation_id UUID NOT NULL,
	completed_turns INTEGER NOT NULL,
	through_message_id UUID NOT NULL,
	body TEXT NOT NULL,
	source_refs JSONB NOT NULL,
	model VARCHAR(200) NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_conversation_summaries PRIMARY KEY (id),
	CONSTRAINT uq_conversation_summaries_turns UNIQUE (conversation_id, completed_turns),
	CONSTRAINT fk_conversation_summaries_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
	CONSTRAINT fk_conversation_summaries_through_message_id_messages FOREIGN KEY(through_message_id) REFERENCES messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_conversation_summaries_conversation_id ON conversation_summaries (conversation_id);

CREATE TABLE daily_checkins (
	owner_id UUID NOT NULL,
	local_date DATE NOT NULL,
	status VARCHAR(20) DEFAULT 'active' NOT NULL,
	prompts JSONB DEFAULT '[]'::jsonb NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	capture_id UUID,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_daily_checkins PRIMARY KEY (id),
	CONSTRAINT uq_daily_checkins_owner_date UNIQUE (owner_id, local_date),
	CONSTRAINT ck_daily_checkins_status CHECK (status IN ('active','completed','skipped')),
	CONSTRAINT fk_daily_checkins_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_daily_checkins_capture_id_personal_captures FOREIGN KEY(capture_id) REFERENCES personal_captures (id) ON DELETE SET NULL
);

CREATE INDEX ix_daily_checkins_owner_id ON daily_checkins (owner_id);

CREATE TABLE knowledge_chunks (
	revision_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	body TEXT NOT NULL,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	location JSONB NOT NULL,
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, body)) STORED NOT NULL,
	embedding VECTOR,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id),
	CONSTRAINT uq_knowledge_chunks_revision_ordinal UNIQUE (revision_id, ordinal),
	CONSTRAINT ck_knowledge_chunks_offsets CHECK (ordinal >= 0 AND start_offset >= 0 AND end_offset >= start_offset),
	CONSTRAINT fk_knowledge_chunks_revision_id_document_revisions FOREIGN KEY(revision_id) REFERENCES document_revisions (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_chunks_embedding_1536 ON knowledge_chunks USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE vector_dims(embedding) = 1536;

CREATE INDEX ix_knowledge_chunks_revision_id ON knowledge_chunks (revision_id);

CREATE INDEX ix_knowledge_chunks_search ON knowledge_chunks USING gin (search_vector);

CREATE TABLE personal_tasks (
	owner_id UUID NOT NULL,
	capture_id UUID NOT NULL,
	title VARCHAR(300) NOT NULL,
	status VARCHAR(20) DEFAULT 'proposed' NOT NULL,
	suggested_due_at TIMESTAMP WITH TIME ZONE,
	schedule_basis VARCHAR(80) DEFAULT 'unscheduled' NOT NULL,
	reminder_id UUID,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_personal_tasks PRIMARY KEY (id),
	CONSTRAINT uq_personal_tasks_capture UNIQUE (capture_id),
	CONSTRAINT fk_personal_tasks_capture_owner FOREIGN KEY(capture_id, owner_id) REFERENCES personal_captures (id, owner_id) ON DELETE CASCADE,
	CONSTRAINT ck_personal_tasks_status CHECK (status IN ('proposed','open','done','dismissed')),
	CONSTRAINT fk_personal_tasks_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_personal_tasks_reminder_id_reminders FOREIGN KEY(reminder_id) REFERENCES reminders (id) ON DELETE SET NULL
);

CREATE INDEX ix_personal_tasks_owner_id ON personal_tasks (owner_id);

CREATE TABLE reports (
	owner_id UUID,
	workspace_id UUID,
	job_run_id UUID,
	title VARCHAR(300) NOT NULL,
	body TEXT NOT NULL,
	source_refs JSONB DEFAULT '[]'::jsonb NOT NULL,
	result_data JSONB DEFAULT '{}'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_reports PRIMARY KEY (id),
	CONSTRAINT ck_reports_audience_xor CHECK ((owner_id IS NOT NULL) <> (workspace_id IS NOT NULL)),
	CONSTRAINT uq_reports_job_run UNIQUE (job_run_id),
	CONSTRAINT fk_reports_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE,
	CONSTRAINT fk_reports_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT fk_reports_job_run_id_job_runs FOREIGN KEY(job_run_id) REFERENCES job_runs (id) ON DELETE SET NULL
);

CREATE INDEX ix_reports_owner_id ON reports (owner_id);

CREATE INDEX ix_reports_workspace_id ON reports (workspace_id);

CREATE TABLE agent_checkpoint_writes (
	run_id UUID NOT NULL,
	namespace VARCHAR(200) NOT NULL,
	checkpoint_id VARCHAR(100) NOT NULL,
	task_id VARCHAR(100) NOT NULL,
	index INTEGER NOT NULL,
	channel VARCHAR(200) NOT NULL,
	payload_type VARCHAR(30) NOT NULL,
	payload BYTEA NOT NULL,
	CONSTRAINT pk_agent_checkpoint_writes PRIMARY KEY (run_id, namespace, checkpoint_id, task_id, index),
	CONSTRAINT fk_agent_checkpoint_writes_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);

CREATE TABLE agent_checkpoints (
	run_id UUID NOT NULL,
	namespace VARCHAR(200) NOT NULL,
	checkpoint_id VARCHAR(100) NOT NULL,
	parent_id VARCHAR(100),
	payload_type VARCHAR(30) NOT NULL,
	payload BYTEA NOT NULL,
	metadata_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_agent_checkpoints PRIMARY KEY (run_id, namespace, checkpoint_id),
	CONSTRAINT fk_agent_checkpoints_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);

CREATE TABLE agent_run_events (
	run_id UUID NOT NULL,
	stage VARCHAR(60) NOT NULL,
	detail JSONB DEFAULT '{}'::jsonb NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_agent_run_events PRIMARY KEY (id),
	CONSTRAINT fk_agent_run_events_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_run_events_run_id ON agent_run_events (run_id);
