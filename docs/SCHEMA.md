# Schema and ORM guide

PostgreSQL is the system of record. Use Alembic migrations, not this reference DDL, to install or upgrade. New tables use foreign keys, constrained statuses and indexes for their access/run paths. Repositories and services remain responsible for authorization; a foreign key is not an access grant.

## Model modules

| Module | Responsibility |
| --- | --- |
| `app/models.py` | Identity, scopes, workspace membership, personal/shared resources, reports, jobs, incidents and audit |
| `app/models_agent.py` | Durable agent runs, checkpoints, pending writes, run events and tool invocation records |
| `app/models_knowledge.py` | Briefs, preferences, conversation summaries, document revisions and embeddings |
| `app/models_personal.py` | Captures, tasks, actions, folders, organization changes and check-ins |
| `app/models_connections.py` | Connections, OAuth state, cached calendars, meetings and delivery outbox |
| `app/models_governance.py` | Scoped delegation and job revision approval |

## Table locator

| Table | ORM class | Columns | Foreign-key targets |
| --- | --- | ---: | --- |
| `agent_checkpoint_writes` | `app.models_agent.AgentCheckpointWrite` | 8 | agent_runs.id |
| `agent_checkpoints` | `app.models_agent.AgentCheckpoint` | 8 | agent_runs.id |
| `agent_run_events` | `app.models_agent.AgentRunEvent` | 5 | agent_runs.id |
| `agent_runs` | `app.models_agent.AgentRun` | 25 | conversations.id, messages.id, projects.id, users.id, workspaces.id |
| `app_settings` | `app.models.AppSetting` | 5 | — |
| `assistant_settings` | `app.models_personal.AssistantSettings` | 10 | users.id |
| `audit_events` | `app.models.AuditEvent` | 7 | users.id |
| `auth_sessions` | `app.models.AuthSession` | 6 | users.id |
| `calendar_events` | `app.models_connections.CalendarEvent` | 13 | connections.id, users.id |
| `connections` | `app.models_connections.Connection` | 13 | users.id, workspaces.id |
| `conversation_summaries` | `app.models_knowledge.ConversationSummary` | 8 | conversations.id, messages.id |
| `conversations` | `app.models.Conversation` | 7 | projects.id, projects.owner_id, users.id, workspaces.id |
| `daily_checkins` | `app.models_personal.DailyCheckin` | 9 | personal_captures.id, users.id |
| `deliveries` | `app.models_connections.Delivery` | 17 | connections.id, users.id |
| `document_chunks` | `app.models.DocumentChunk` | 5 | documents.id |
| `document_revisions` | `app.models_knowledge.DocumentRevision` | 23 | documents.id, users.id |
| `documents` | `app.models.Document` | 12 | projects.id, projects.owner_id, users.id, workspaces.id |
| `incident_events` | `app.models.IncidentEvent` | 6 | incidents.id, users.id |
| `incidents` | `app.models.Incident` | 15 | users.id, workspaces.id |
| `job_dependencies` | `app.models.JobDependency` | 3 | scheduled_jobs.id |
| `job_revisions` | `app.models_governance.JobRevision` | 11 | scheduled_jobs.id, users.id |
| `job_runs` | `app.models.JobRun` | 16 | scheduled_jobs.id |
| `knowledge_chunks` | `app.models_knowledge.KnowledgeChunk` | 10 | document_revisions.id |
| `login_attempts` | `app.models.LoginAttempt` | 5 | — |
| `meeting_artifacts` | `app.models_connections.MeetingArtifact` | 9 | meeting_rooms.id, users.id |
| `meeting_attendees` | `app.models_connections.MeetingAttendee` | 3 | meeting_rooms.id, users.id |
| `meeting_distributions` | `app.models_connections.MeetingDistribution` | 7 | meeting_rooms.id |
| `meeting_extractions` | `app.models_connections.MeetingExtraction` | 18 | meeting_rooms.id, users.id |
| `meeting_rooms` | `app.models_connections.MeetingRoom` | 14 | users.id |
| `memberships` | `app.models.Membership` | 4 | users.id, workspaces.id |
| `memories` | `app.models.Memory` | 7 | notes.id, notes.owner_id, users.id |
| `messages` | `app.models.Message` | 9 | conversations.id, users.id |
| `note_placements` | `app.models_personal.NotePlacement` | 5 | notes.id, notes.owner_id, project_folders.id, project_folders.owner_id, project_folders.project_id, users.id |
| `notes` | `app.models.Note` | 8 | projects.id, projects.owner_id, users.id |
| `notifications` | `app.models.Notification` | 10 | users.id |
| `oauth_attempts` | `app.models_connections.OAuthAttempt` | 7 | connections.id |
| `organization_changes` | `app.models_personal.OrganizationChange` | 7 | personal_actions.id, users.id |
| `personal_actions` | `app.models_personal.PersonalAction` | 11 | users.id |
| `personal_captures` | `app.models_personal.PersonalCapture` | 16 | notes.id, users.id |
| `personal_tasks` | `app.models_personal.PersonalTask` | 10 | personal_captures.id, personal_captures.owner_id, reminders.id, users.id |
| `project_folders` | `app.models_personal.ProjectFolder` | 7 | project_folders.id, project_folders.owner_id, project_folders.project_id, projects.id, projects.owner_id, users.id |
| `projects` | `app.models.Project` | 7 | users.id |
| `reminders` | `app.models.Reminder` | 9 | notes.id, notes.owner_id, users.id |
| `reports` | `app.models.Report` | 9 | job_runs.id, users.id, workspaces.id |
| `scheduled_jobs` | `app.models.ScheduledJob` | 21 | users.id, workspaces.id |
| `scope_grants` | `app.models.ScopeGrant` | 4 | scopes.id, users.id |
| `scope_role_assignments` | `app.models_governance.ScopeRoleAssignment` | 10 | scopes.id, users.id |
| `scopes` | `app.models.Scope` | 6 | scopes.id |
| `subscriptions` | `app.models.Subscription` | 3 | scopes.id, users.id |
| `user_preferences` | `app.models_knowledge.UserPreference` | 9 | users.id |
| `users` | `app.models.User` | 10 | — |
| `workspace_automations` | `app.models_governance.WorkspaceAutomation` | 4 | users.id, workspaces.id |
| `workspace_briefs` | `app.models_knowledge.WorkspaceBrief` | 9 | users.id, workspaces.id |
| `workspaces` | `app.models.Workspace` | 9 | scopes.id, users.id |

## Relationships and lifecycle

- A scope has an optional parent; a workspace belongs to one scope. Membership and explicit grants govern raw/shared context and management summaries.
- A conversation belongs to a personal owner or workspace audience. Agent runs and citations retain that audience; checkpoints are execution state, not source-of-truth business records.
- A document has revisions. New revisions retain original bytes, extraction/chunks and embedding profile/state. Deleting or replacing evidence must not silently broaden access to its historical references.
- A capture preserves the original note and links its interpreted tasks, reminders and memories. Action and organization journals provide idempotency and conflict-aware reversal.
- A connection has one authorized owner/audience and encrypted secrets. Calendar occurrences and provider artifacts retain their connection/provider identifiers.
- A Meeting Room retains participants and artifact/minutes revisions. Approved publication and personal acceptance are separate operations.
- An approved analytical job revision defines constrained execution authority. Lease/run records govern retry ownership; a completed text response does not imply a validation pass.

See [CODE_MAP.md](CODE_MAP.md) for repository/service methods and [schema.sql](schema.sql) for the compiled constraints and indexes.
