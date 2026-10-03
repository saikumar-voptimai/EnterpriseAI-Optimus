# Code navigation map

Generated from the Python source by `python3 scripts/generate_reference.py`. Use [ARCHITECTURE.md](ARCHITECTURE.md) for execution flow and this map to locate an implementation. Only public methods are listed; internal helpers remain in the linked module.

## [backend/app/agents/actions.py](../backend/app/agents/actions.py)

| Object | Public methods / role |
| --- | --- |
| `ActionContext` | Declarative model, schema or state definition |
| `explicit_intent()` | Module function |
| `deadline_matches()` | Module function |
| `PersonalActionTools` | `__init__()`, `execute()` |

## [backend/app/agents/catalog.py](../backend/app/agents/catalog.py)

| Object | Public methods / role |
| --- | --- |
| `SkillCatalog` | `select()` |

## [backend/app/agents/checkpoints.py](../backend/app/agents/checkpoints.py)

| Object | Public methods / role |
| --- | --- |
| `LeaseLost` | `__init__()` |
| `FencedCheckpointSaver` | `__init__()`, `get_tuple()`, `put()`, `put_writes()`, `list()`, `aget_tuple()`, `aput()`, `aput_writes()`, `alist()` |

## [backend/app/agents/runtime.py](../backend/app/agents/runtime.py)

| Object | Public methods / role |
| --- | --- |
| `AgentLease` | Declarative model, schema or state definition |
| `AgentResult` | Declarative model, schema or state definition |
| `AgentState` | Declarative model, schema or state definition |
| `merge_sources()` | Module function |
| `AgentRunner` | `__init__()`, `claim_one()`, `prune_checkpoints()`, `heartbeat()`, `run_bundle()`, `execute()` |

## [backend/app/agents/tools.py](../backend/app/agents/tools.py)

| Object | Public methods / role |
| --- | --- |
| `Arguments` | Declarative model, schema or state definition |
| `SearchArgs` | Declarative model, schema or state definition |
| `IncidentArgs` | Declarative model, schema or state definition |
| `ReportArgs` | Declarative model, schema or state definition |
| `CalculateArgs` | Declarative model, schema or state definition |
| `NoteArgs` | Declarative model, schema or state definition |
| `ReminderArgs` | Declarative model, schema or state definition |
| `CalendarArgs` | Declarative model, schema or state definition |
| `ConnectionsArgs` | Declarative model, schema or state definition |
| `DescribeArgs` | Declarative model, schema or state definition |
| `SeriesArgs` | Declarative model, schema or state definition |
| `ToolResult` | Declarative model, schema or state definition |
| `ToolRegistry` | `__init__()`, `schemas()`, `invoke()` |

## [backend/app/api_agents.py](../backend/app/api_agents.py)

| Object | Public methods / role |
| --- | --- |
| `RunCreate` | Declarative model, schema or state definition |
| `PreviewRequest` | Declarative model, schema or state definition |
| `create_run()` | Module function |
| `get_run()` | Module function |
| `cancel_run()` | Module function |
| `list_runs()` | Module function |
| `preview()` | Module function |

## [backend/app/api_connections.py](../backend/app/api_connections.py)

| Object | Public methods / role |
| --- | --- |
| `Strict` | Declarative model, schema or state definition |
| `ConnectionCreate` | Declarative model, schema or state definition |
| `ConnectionUpdate` | Declarative model, schema or state definition |
| `MicrosoftStart` | Declarative model, schema or state definition |
| `DriveImport` | Declarative model, schema or state definition |
| `SeriesQuery` | Declarative model, schema or state definition |
| `DeliveryCreate` | Declarative model, schema or state definition |
| `delivery_public()` | Module function |
| `capabilities()` | Module function |
| `connections()` | Module function |
| `create_connection()` | Module function |
| `update_connection()` | Module function |
| `disconnect()` | Module function |
| `microsoft_start()` | Module function |
| `microsoft_callback()` | Module function |
| `google_start()` | Module function |
| `google_callback()` | Module function |
| `drive_files()` | Module function |
| `drive_import()` | Module function |
| `resources()` | Module function |
| `sync()` | Module function |
| `series()` | Module function |
| `calendar_events()` | Module function |
| `transcribe()` | Module function |
| `deliveries()` | Module function |
| `enqueue()` | Module function |
| `undo_delivery()` | Module function |

## [backend/app/api_governance.py](../backend/app/api_governance.py)

| Object | Public methods / role |
| --- | --- |
| `Setup` | Declarative model, schema or state definition |
| `setup_status()` | Module function |
| `setup()` | Module function |
| `Delegation` | `aware()` |
| `delegations()` | Module function |
| `delegate()` | Module function |
| `revoke()` | Module function |
| `Review` | Declarative model, schema or state definition |
| `locked_job()` | Module function |
| `submit()` | Module function |
| `approve()` | Module function |
| `reject()` | Module function |
| `undo_approval()` | Module function |
| `templates()` | Module function |
| `HierarchyLabels` | `valid_labels()` |
| `hierarchy_labels()` | Module function |
| `update_hierarchy_labels()` | Module function |
| `JobEdit` | Declarative model, schema or state definition |
| `edit_job()` | Module function |

## [backend/app/api_job_drafts.py](../backend/app/api_job_drafts.py)

| Object | Public methods / role |
| --- | --- |
| `JobDraftRequest` | Declarative model, schema or state definition |
| `draft_job()` | Module function |

## [backend/app/api_knowledge.py](../backend/app/api_knowledge.py)

| Object | Public methods / role |
| --- | --- |
| `BriefDraft` | Declarative model, schema or state definition |
| `GenerateBrief` | Declarative model, schema or state definition |
| `PreferenceUpdate` | Declarative model, schema or state definition |
| `brief_payload()` | Module function |
| `index_payload()` | Module function |
| `preferences()` | Module function |
| `update_preferences()` | Module function |
| `get_brief()` | Module function |
| `draft_brief()` | Module function |
| `generate_brief()` | Module function |
| `publish_brief()` | Module function |
| `context_preview()` | Module function |
| `document_index()` | Module function |
| `retry_index()` | Module function |
| `document_revisions()` | Module function |
| `upload_revision()` | Module function |
| `download_original()` | Module function |

## [backend/app/api_meetings.py](../backend/app/api_meetings.py)

| Object | Public methods / role |
| --- | --- |
| `MeetingCreate` | Declarative model, schema or state definition |
| `ArtifactCreate` | Declarative model, schema or state definition |
| `DraftRequest` | Declarative model, schema or state definition |
| `ReviewRequest` | Declarative model, schema or state definition |
| `ProviderArtifactRequest` | Declarative model, schema or state definition |
| `meeting_people()` | Module function |
| `meetings()` | Module function |
| `create()` | Module function |
| `meeting()` | Module function |
| `import_artifact()` | Module function |
| `discover_provider_artifacts()` | Module function |
| `provider_artifact()` | Module function |
| `draft()` | Module function |
| `review()` | Module function |
| `publish()` | Module function |
| `accept()` | Module function |
| `archive()` | Module function |
| `cancel_extraction()` | Module function |
| `artifact_content()` | Module function |

## [backend/app/api_operations.py](../backend/app/api_operations.py)

| Object | Public methods / role |
| --- | --- |
| `row()` | Module function |
| `public_user()` | Module function |
| `access()` | Module function |
| `audit()` | Module function |
| `commit()` | Module function |
| `notification_payload()` | Module function |
| `incidents()` | Module function |
| `create_incident()` | Module function |
| `incident_action()` | Module function |
| `publications()` | Module function |
| `subscriptions()` | Module function |
| `update_subscriptions()` | Module function |
| `notifications()` | Module function |
| `read_notification()` | Module function |
| `job_payload()` | Module function |
| `jobs()` | Module function |
| `create_job()` | Module function |
| `update_job()` | Module function |
| `run_job()` | Module function |
| `job_runs()` | Module function |
| `reports()` | Module function |
| `users()` | Module function |
| `create_user()` | Module function |
| `update_user()` | Module function |
| `admin_scopes()` | Module function |
| `create_scope()` | Module function |
| `create_scope_grant()` | Module function |
| `scope_grants()` | Module function |
| `revoke_scope_grant()` | Module function |
| `admin_audit()` | Module function |

## [backend/app/api_personal.py](../backend/app/api_personal.py)

| Object | Public methods / role |
| --- | --- |
| `Strict` | Declarative model, schema or state definition |
| `CaptureCreate` | Declarative model, schema or state definition |
| `ClassifyRequest` | Declarative model, schema or state definition |
| `CaptureApply` | Declarative model, schema or state definition |
| `SettingsUpdate` | `valid_timezone()` |
| `FolderCreate` | Declarative model, schema or state definition |
| `OrganizationPreview` | Declarative model, schema or state definition |
| `CheckinComplete` | Declarative model, schema or state definition |
| `TaskUpdate` | Declarative model, schema or state definition |
| `settings()` | Module function |
| `update_settings()` | Module function |
| `capture()` | Module function |
| `captures()` | Module function |
| `get_capture()` | Module function |
| `classify()` | Module function |
| `apply_capture()` | Module function |
| `tasks()` | Module function |
| `update_task()` | Module function |
| `folders()` | Module function |
| `create_folder()` | Module function |
| `placements()` | Module function |
| `actions()` | Module function |
| `undo()` | Module function |
| `organization()` | Module function |
| `organization_preview()` | Module function |
| `organization_apply()` | Module function |
| `organization_undo()` | Module function |
| `checkins()` | Module function |
| `start_checkin()` | Module function |
| `complete_checkin()` | Module function |

## [backend/app/auth.py](../backend/app/auth.py)

| Object | Public methods / role |
| --- | --- |
| `utcnow()` | Module function |
| `hash_password()` | Module function |
| `verify_password()` | Module function |
| `token_hash()` | Module function |
| `require_origin()` | Module function |
| `rate_limit_login()` | Module function |
| `Actor` | Declarative model, schema or state definition |
| `current_actor()` | Module function |
| `admin_actor()` | Module function |
| `new_session()` | Module function |

## [backend/app/cli.py](../backend/app/cli.py)

| Object | Public methods / role |
| --- | --- |
| `main()` | Module function |

## [backend/app/config.py](../backend/app/config.py)

| Object | Public methods / role |
| --- | --- |
| `Settings` | `postgres_only()`, `origin_only()`, `resolve_model_tiers()`, `connect_options()`, `allowed_models()`, `tier_models()`, `model_choices()`, `resolve_model()`, `public_model()` |
| `get_settings()` | Module function |

## [backend/app/connectors/base.py](../backend/app/connectors/base.py)

| Object | Public methods / role |
| --- | --- |
| `CredentialVault` | `__init__()`, `encrypt()`, `decrypt()` |
| `validate_url()` | Module function |
| `ConnectorHTTP` | `__init__()`, `request()`, `json()` |

## [backend/app/connectors/google.py](../backend/app/connectors/google.py)

| Object | Public methods / role |
| --- | --- |
| `GoogleAdapter` | `__init__()`, `redirect_uri()`, `authorize_url()`, `token()`, `get()`, `pages()`, `account()`, `calendars()`, `events()`, `drive_files()`, `drive_download()`, `meet_transcripts()`, `meet_transcript_text()` |
| `google_datetime()` | Module function |
| `google_event_values()` | Module function |

## [backend/app/connectors/influxdb.py](../backend/app/connectors/influxdb.py)

| Object | Public methods / role |
| --- | --- |
| `InfluxAdapter` | `__init__()`, `resources()`, `bucket_names()`, `describe()`, `query_series()` |

## [backend/app/connectors/microsoft.py](../backend/app/connectors/microsoft.py)

| Object | Public methods / role |
| --- | --- |
| `MicrosoftAdapter` | `__init__()`, `redirect_uri()`, `authority()`, `authorize_url()`, `token()`, `get()`, `pages()`, `calendars()`, `events()`, `transcript()`, `transcripts()` |
| `graph_datetime()` | Module function |

## [backend/app/connectors/zoom.py](../backend/app/connectors/zoom.py)

| Object | Public methods / role |
| --- | --- |
| `ZoomAdapter` | `__init__()`, `token()`, `recordings()`, `transcript()` |

## [backend/app/db.py](../backend/app/db.py)

| Object | Public methods / role |
| --- | --- |
| `get_session()` | Module function |

## [backend/app/db_hardening.py](../backend/app/db_hardening.py)

| Object | Public methods / role |
| --- | --- |
| `lock_down_schema()` | Module function |

## [backend/app/knowledge_cli.py](../backend/app/knowledge_cli.py)

| Object | Public methods / role |
| --- | --- |
| `backfill()` | Module function |
| `qdrant_sync()` | Module function |
| `main()` | Module function |

## [backend/app/main.py](../backend/app/main.py)

| Object | Public methods / role |
| --- | --- |
| `row()` | Module function |
| `public_user()` | Module function |
| `audit()` | Module function |
| `private()` | Module function |
| `access()` | Module function |
| `workspace_payload()` | Module function |
| `project_check()` | Module function |
| `commit()` | Module function |
| `forbidden()` | Module function |
| `missing()` | Module function |
| `conflict()` | Module function |
| `invalid_data()` | Module function |
| `database_failure()` | Module function |
| `security_headers()` | Module function |
| `live()` | Module function |
| `ready()` | Module function |
| `login()` | Module function |
| `me()` | Module function |
| `logout()` | Module function |
| `change_password()` | Module function |
| `bootstrap()` | Module function |
| `directory()` | Module function |
| `projects()` | Module function |
| `create_project()` | Module function |
| `update_project()` | Module function |
| `notes()` | Module function |
| `create_note()` | Module function |
| `update_note()` | Module function |
| `memories()` | Module function |
| `create_memory()` | Module function |
| `update_memory()` | Module function |
| `forget_memory()` | Module function |
| `reminders()` | Module function |
| `create_reminder()` | Module function |
| `update_reminder()` | Module function |
| `scopes()` | Module function |
| `workspaces()` | Module function |
| `create_workspace()` | Module function |
| `update_workspace()` | Module function |
| `members()` | Module function |
| `add_member()` | Module function |
| `remove_member()` | Module function |
| `service_error()` | Module function |
| `validate_audience()` | Module function |
| `documents()` | Module function |
| `upload_document()` | Module function |
| `document()` | Module function |
| `delete_document()` | Module function |
| `conversations()` | Module function |
| `create_conversation()` | Module function |
| `messages()` | Module function |
| `chat_allowed()` | Module function |
| `send_message()` | Module function |
| `frontend()` | Module function |

## [backend/app/middleware.py](../backend/app/middleware.py)

| Object | Public methods / role |
| --- | --- |
| `RequestSizeLimit` | `__init__()` |

## [backend/app/models.py](../backend/app/models.py)

| Object | Public methods / role |
| --- | --- |
| `utcnow()` | Module function |
| `uuid_string()` | Module function |
| `Base` | Declarative model, schema or state definition |
| `Created` | Declarative model, schema or state definition |
| `Identified` | Declarative model, schema or state definition |
| `Mutable` | Declarative model, schema or state definition |
| `User` | Declarative model, schema or state definition |
| `AuthSession` | Declarative model, schema or state definition |
| `LoginAttempt` | Declarative model, schema or state definition |
| `Scope` | Declarative model, schema or state definition |
| `ScopeGrant` | Declarative model, schema or state definition |
| `Workspace` | Declarative model, schema or state definition |
| `Membership` | Declarative model, schema or state definition |
| `Project` | Declarative model, schema or state definition |
| `Note` | Declarative model, schema or state definition |
| `Memory` | Declarative model, schema or state definition |
| `Reminder` | Declarative model, schema or state definition |
| `Document` | Declarative model, schema or state definition |
| `DocumentChunk` | Declarative model, schema or state definition |
| `Conversation` | Declarative model, schema or state definition |
| `Message` | Declarative model, schema or state definition |
| `Incident` | Declarative model, schema or state definition |
| `IncidentEvent` | Declarative model, schema or state definition |
| `Subscription` | Declarative model, schema or state definition |
| `Notification` | Declarative model, schema or state definition |
| `ScheduledJob` | Declarative model, schema or state definition |
| `JobDependency` | Declarative model, schema or state definition |
| `JobRun` | Declarative model, schema or state definition |
| `Report` | Declarative model, schema or state definition |
| `AuditEvent` | Declarative model, schema or state definition |
| `AppSetting` | Declarative model, schema or state definition |

## [backend/app/models_agent.py](../backend/app/models_agent.py)

| Object | Public methods / role |
| --- | --- |
| `AgentRun` | Declarative model, schema or state definition |
| `AgentRunEvent` | Declarative model, schema or state definition |
| `AgentCheckpoint` | Declarative model, schema or state definition |
| `AgentCheckpointWrite` | Declarative model, schema or state definition |

## [backend/app/models_connections.py](../backend/app/models_connections.py)

| Object | Public methods / role |
| --- | --- |
| `Connection` | Declarative model, schema or state definition |
| `OAuthAttempt` | Declarative model, schema or state definition |
| `CalendarEvent` | Declarative model, schema or state definition |
| `MeetingRoom` | Declarative model, schema or state definition |
| `MeetingAttendee` | Declarative model, schema or state definition |
| `MeetingArtifact` | Declarative model, schema or state definition |
| `MeetingDistribution` | Declarative model, schema or state definition |
| `Delivery` | Declarative model, schema or state definition |
| `MeetingExtraction` | Declarative model, schema or state definition |

## [backend/app/models_governance.py](../backend/app/models_governance.py)

| Object | Public methods / role |
| --- | --- |
| `ScopeRoleAssignment` | Declarative model, schema or state definition |
| `WorkspaceAutomation` | Declarative model, schema or state definition |
| `JobRevision` | Declarative model, schema or state definition |

## [backend/app/models_knowledge.py](../backend/app/models_knowledge.py)

| Object | Public methods / role |
| --- | --- |
| `WorkspaceBrief` | Declarative model, schema or state definition |
| `UserPreference` | Declarative model, schema or state definition |
| `DocumentRevision` | Declarative model, schema or state definition |
| `KnowledgeChunk` | Declarative model, schema or state definition |
| `ConversationSummary` | Declarative model, schema or state definition |

## [backend/app/models_personal.py](../backend/app/models_personal.py)

| Object | Public methods / role |
| --- | --- |
| `AssistantSettings` | Declarative model, schema or state definition |
| `PersonalCapture` | Declarative model, schema or state definition |
| `PersonalTask` | Declarative model, schema or state definition |
| `ProjectFolder` | Declarative model, schema or state definition |
| `NotePlacement` | Declarative model, schema or state definition |
| `PersonalAction` | Declarative model, schema or state definition |
| `OrganizationChange` | Declarative model, schema or state definition |
| `DailyCheckin` | Declarative model, schema or state definition |

## [backend/app/repositories/access.py](../backend/app/repositories/access.py)

| Object | Public methods / role |
| --- | --- |
| `AuthorizationError` | Declarative model, schema or state definition |
| `NotFound` | Declarative model, schema or state definition |
| `AccessRepository` | `__init__()`, `require_active()`, `workspace_ids()`, `accessible_workspaces()`, `reviewable_scopes()`, `workspace()`, `project()`, `note()`, `memory()`, `reminder()`, `document()`, `conversation()`, `report()`, `source_refs_authorized()`, `source_records_allowed()`, `job()`, `job_run()`, `incident()`, `notification()`, `publication_statement()`, `visible_publications()`, `publication()` |

## [backend/app/repositories/connections.py](../backend/app/repositories/connections.py)

| Object | Public methods / role |
| --- | --- |
| `ConnectionRepository` | `__init__()`, `list()`, `get()`, `meeting()` |

## [backend/app/repositories/knowledge.py](../backend/app/repositories/knowledge.py)

| Object | Public methods / role |
| --- | --- |
| `KnowledgeRepository` | `__init__()`, `documents_predicate()`, `chunks()`, `current_revision()` |

## [backend/app/repositories/personal_assistant.py](../backend/app/repositories/personal_assistant.py)

| Object | Public methods / role |
| --- | --- |
| `record()` | Module function |
| `PersonalAssistantRepository` | `__init__()`, `settings()`, `owned()`, `action_by_request()`, `capture_by_request()`, `folder()`, `lock_owner()` |

## [backend/app/repositories/private.py](../backend/app/repositories/private.py)

| Object | Public methods / role |
| --- | --- |
| `PrivateRepository` | `__init__()`, `projects()`, `create_project()`, `notes()`, `create_note()`, `memories()`, `create_memory()`, `forget_memory()`, `reminders()`, `create_reminder()` |

## [backend/app/repositories/workspaces.py](../backend/app/repositories/workspaces.py)

| Object | Public methods / role |
| --- | --- |
| `WorkspaceRepository` | `__init__()`, `reviewable_scopes()`, `members()`, `documents()`, `conversations()`, `create_conversation()`, `messages()`, `incidents()`, `jobs()`, `job_runs()`, `reports()` |

## [backend/app/schemas.py](../backend/app/schemas.py)

| Object | Public methods / role |
| --- | --- |
| `uuid_string()` | Module function |
| `Input` | Declarative model, schema or state definition |
| `Login` | Declarative model, schema or state definition |
| `Password` | Declarative model, schema or state definition |
| `UserCreate` | `trim_identity()` |
| `UserUpdate` | Declarative model, schema or state definition |
| `ScopeCreate` | Declarative model, schema or state definition |
| `GrantCreate` | Declarative model, schema or state definition |
| `ProjectCreate` | Declarative model, schema or state definition |
| `ProjectUpdate` | Declarative model, schema or state definition |
| `NoteCreate` | Declarative model, schema or state definition |
| `NoteUpdate` | Declarative model, schema or state definition |
| `MemoryCreate` | Declarative model, schema or state definition |
| `MemoryUpdate` | Declarative model, schema or state definition |
| `ReminderCreate` | `aware()` |
| `ReminderUpdate` | `aware()` |
| `WorkspaceCreate` | Declarative model, schema or state definition |
| `WorkspaceUpdate` | Declarative model, schema or state definition |
| `MemberCreate` | Declarative model, schema or state definition |
| `ConversationCreate` | Declarative model, schema or state definition |
| `ChatSend` | Declarative model, schema or state definition |
| `IncidentCreate` | Declarative model, schema or state definition |
| `IncidentAction` | Declarative model, schema or state definition |
| `SubscriptionUpdate` | Declarative model, schema or state definition |
| `JobCreate` | `aware()` |
| `JobUpdate` | Declarative model, schema or state definition |

## [backend/app/services/actions.py](../backend/app/services/actions.py)

| Object | Public methods / role |
| --- | --- |
| `aware()` | Module function |
| `ActionService` | `__init__()`, `create_note()`, `create_reminder()`, `create_memory()`, `undo()` |

## [backend/app/services/agent_runs.py](../backend/app/services/agent_runs.py)

| Object | Public methods / role |
| --- | --- |
| `AgentRunService` | `__init__()`, `authorize_conversation()`, `enqueue_chat()`, `get()`, `cancel()`, `serialize()` |

## [backend/app/services/calendar_sync.py](../backend/app/services/calendar_sync.py)

| Object | Public methods / role |
| --- | --- |
| `microsoft_event_values()` | Module function |
| `CalendarSyncService` | `__init__()`, `sync()`, `tick()`, `events()`, `free_slots()` |

## [backend/app/services/capture.py](../backend/app/services/capture.py)

| Object | Public methods / role |
| --- | --- |
| `Classification` | Declarative model, schema or state definition |
| `capture_zone()` | Module function |
| `suggested_reminder_time()` | Module function |
| `CaptureService` | `__init__()`, `save()`, `payload()`, `classify()`, `apply()` |

## [backend/app/services/connections.py](../backend/app/services/connections.py)

| Object | Public methods / role |
| --- | --- |
| `slack_webhook()` | Module function |
| `pkce_pair()` | Module function |
| `ConnectionService` | `__init__()`, `vault()`, `credentials()`, `public()`, `list()`, `create()`, `update()`, `disconnect()`, `start_microsoft()`, `finish_microsoft()`, `microsoft_token()`, `start_google()`, `finish_google()`, `google_token()`, `drive_files()`, `import_drive_file()`, `resources()`, `bucket_names()`, `describe_series()`, `query_series()` |

## [backend/app/services/context.py](../backend/app/services/context.py)

| Object | Public methods / role |
| --- | --- |
| `ContextBundle` | Declarative model, schema or state definition |
| `ContextService` | `__init__()`, `for_conversation()`, `for_job()`, `configuration()`, `validate_metadata()`, `summarize_if_due()` |

## [backend/app/services/delivery.py](../backend/app/services/delivery.py)

| Object | Public methods / role |
| --- | --- |
| `email_address()` | Module function |
| `DeliveryService` | `__init__()`, `enqueue()`, `cancel()`, `tick()` |

## [backend/app/services/documents.py](../backend/app/services/documents.py)

| Object | Public methods / role |
| --- | --- |
| `ExtractedDocument` | Declarative model, schema or state definition |
| `extract_document()` | Module function |
| `ChunkFragment` | Declarative model, schema or state definition |
| `chunk_text()` | Module function |

## [backend/app/services/errors.py](../backend/app/services/errors.py)

| Object | Public methods / role |
| --- | --- |
| `ServiceError` | `__init__()` |
| `ProviderError` | `__init__()` |

## [backend/app/services/gateway.py](../backend/app/services/gateway.py)

| Object | Public methods / role |
| --- | --- |
| `allowed_models()` | Module function |
| `select_model()` | Module function |
| `Completion` | Declarative model, schema or state definition |
| `ModelTurn` | `message()` |
| `OpenRouterGateway` | `__init__()`, `complete()`, `complete_turn()`, `embed()` |

## [backend/app/services/governance.py](../backend/app/services/governance.py)

| Object | Public methods / role |
| --- | --- |
| `GovernanceService` | `__init__()`, `delegated_scopes()`, `require_scope()`, `require_people_management()`, `snapshot()`, `digest()`, `submit()`, `service_principal()`, `review()`, `require_approved()` |

## [backend/app/services/incidents.py](../backend/app/services/incidents.py)

| Object | Public methods / role |
| --- | --- |
| `notify()` | Module function |
| `AlertRule` | `valid_assignee()`, `valid_escalation()` |
| `validate_alert_rule()` | Module function |
| `IncidentService` | `__init__()`, `publication_notifications()`, `assignee_notification()`, `create()`, `transition()`, `record_finding()` |

## [backend/app/services/ingestion.py](../backend/app/services/ingestion.py)

| Object | Public methods / role |
| --- | --- |
| `IngestionService` | `__init__()`, `ingest()`, `backfill()`, `retry()` |
| `KnowledgeIndexer` | `__init__()`, `run_once()` |

## [backend/app/services/job_drafts.py](../backend/app/services/job_drafts.py)

| Object | Public methods / role |
| --- | --- |
| `StrictModel` | Declarative model, schema or state definition |
| `MetricProposal` | `finite_limits()` |
| `AlertProposal` | Declarative model, schema or state definition |
| `DraftProposal` | Declarative model, schema or state definition |
| `JobDraftService` | `__init__()`, `draft()` |

## [backend/app/services/jobs.py](../backend/app/services/jobs.py)

| Object | Public methods / role |
| --- | --- |
| `utcnow()` | Module function |
| `as_utc()` | Module function |
| `require_execution_authorized()` | Module function |
| `compatible_dependency()` | Module function |
| `dependency_occurrence()` | Module function |
| `validate_job_config()` | Module function |
| `JobService` | `__init__()`, `create()`, `update_dependencies()`, `enqueue()`, `insert_occurrence()` |

## [backend/app/services/meetings.py](../backend/app/services/meetings.py)

| Object | Public methods / role |
| --- | --- |
| `Evidence` | Declarative model, schema or state definition |
| `Decision` | Declarative model, schema or state definition |
| `Action` | `aware_due()` |
| `Minutes` | Declarative model, schema or state definition |
| `render_minutes()` | Module function |
| `MeetingService` | `__init__()`, `list()`, `payload()`, `create()`, `artifacts()`, `import_artifact()`, `validate_minutes()`, `draft()`, `cancel_extraction()`, `process_one()`, `review()`, `publish()`, `accept()`, `archive()` |

## [backend/app/services/operational_reports.py](../backend/app/services/operational_reports.py)

| Object | Public methods / role |
| --- | --- |
| `MetricConfig` | `valid_limits()` |
| `OperationalResult` | Declarative model, schema or state definition |
| `aware()` | Module function |
| `reporting_windows()` | Module function |
| `current_reporting_window()` | Module function |
| `evaluate_series()` | Module function |
| `compare_windows()` | Module function |
| `OperationalReportService` | `__init__()`, `execute()` |

## [backend/app/services/organization.py](../backend/app/services/organization.py)

| Object | Public methods / role |
| --- | --- |
| `topic_name()` | Module function |
| `OrganizationService` | `__init__()`, `create_folder()`, `preview()`, `apply()`, `undo()` |

## [backend/app/services/personal_routines.py](../backend/app/services/personal_routines.py)

| Object | Public methods / role |
| --- | --- |
| `next_local_time()` | Module function |
| `PersonalRoutineService` | `__init__()`, `settings_payload()`, `update_settings()`, `start_checkin()`, `complete_checkin()`, `tick()` |

## [backend/app/services/preferences.py](../backend/app/services/preferences.py)

| Object | Public methods / role |
| --- | --- |
| `PreferenceService` | `__init__()`, `get()`, `update()`, `context()` |

## [backend/app/services/retrieval.py](../backend/app/services/retrieval.py)

| Object | Public methods / role |
| --- | --- |
| `RetrievalService` | `__init__()`, `record()`, `search()`, `search_hybrid()` |

## [backend/app/services/schedules.py](../backend/app/services/schedules.py)

| Object | Public methods / role |
| --- | --- |
| `next_occurrence()` | Module function |
| `previous_occurrence()` | Module function |

## [backend/app/services/vector_index.py](../backend/app/services/vector_index.py)

| Object | Public methods / role |
| --- | --- |
| `audience_key()` | Module function |
| `QdrantIndex` | `__init__()`, `ensure_collection()`, `upsert_revision()`, `search()`, `delete_document()` |

## [backend/app/services/workspace_briefs.py](../backend/app/services/workspace_briefs.py)

| Object | Public methods / role |
| --- | --- |
| `WorkspaceBriefService` | `__init__()`, `published()`, `create_draft()`, `publish()`, `context()`, `generate()` |

## [backend/app/worker.py](../backend/app/worker.py)

| Object | Public methods / role |
| --- | --- |
| `Lease` | Declarative model, schema or state definition |
| `Worker` | `__init__()`, `schedule_due()`, `notify_due_reminders()`, `claim_one()`, `execute()`, `run_once()` |
| `serve()` | Module function |
| `main()` | Module function |

## HTTP route locator

Machine-readable request/response schemas are available at `/api/openapi.json` on the running application.

| Method | Route | Handler |
| --- | --- | --- |
| GET | `/api/admin/audit` | [admin_audit()](../backend/app/api_operations.py) |
| GET | `/api/admin/delegations` | [delegations()](../backend/app/api_governance.py) |
| POST | `/api/admin/delegations` | [delegate()](../backend/app/api_governance.py) |
| DELETE | `/api/admin/delegations/{rid}` | [revoke()](../backend/app/api_governance.py) |
| PUT | `/api/admin/hierarchy-labels` | [update_hierarchy_labels()](../backend/app/api_governance.py) |
| GET | `/api/admin/scope-grants` | [scope_grants()](../backend/app/api_operations.py) |
| POST | `/api/admin/scope-grants` | [create_scope_grant()](../backend/app/api_operations.py) |
| DELETE | `/api/admin/scope-grants/{user_id}/{scope_id}` | [revoke_scope_grant()](../backend/app/api_operations.py) |
| GET | `/api/admin/scopes` | [admin_scopes()](../backend/app/api_operations.py) |
| POST | `/api/admin/scopes` | [create_scope()](../backend/app/api_operations.py) |
| GET | `/api/admin/users` | [users()](../backend/app/api_operations.py) |
| POST | `/api/admin/users` | [create_user()](../backend/app/api_operations.py) |
| PATCH | `/api/admin/users/{rid}` | [update_user()](../backend/app/api_operations.py) |
| GET | `/api/agent-runs/{run_id}` | [get_run()](../backend/app/api_agents.py) |
| POST | `/api/agent-runs/{run_id}/cancel` | [cancel_run()](../backend/app/api_agents.py) |
| POST | `/api/auth/login` | [login()](../backend/app/main.py) |
| POST | `/api/auth/logout` | [logout()](../backend/app/main.py) |
| GET | `/api/auth/me` | [me()](../backend/app/main.py) |
| POST | `/api/auth/password` | [change_password()](../backend/app/main.py) |
| GET | `/api/bootstrap` | [bootstrap()](../backend/app/main.py) |
| GET | `/api/calendar/events` | [calendar_events()](../backend/app/api_connections.py) |
| GET | `/api/connections` | [connections()](../backend/app/api_connections.py) |
| POST | `/api/connections` | [create_connection()](../backend/app/api_connections.py) |
| GET | `/api/connections/capabilities` | [capabilities()](../backend/app/api_connections.py) |
| GET | `/api/connections/google/callback` | [google_callback()](../backend/app/api_connections.py) |
| POST | `/api/connections/google/start` | [google_start()](../backend/app/api_connections.py) |
| GET | `/api/connections/microsoft/callback` | [microsoft_callback()](../backend/app/api_connections.py) |
| POST | `/api/connections/microsoft/start` | [microsoft_start()](../backend/app/api_connections.py) |
| GET | `/api/connections/{connection_id}/meeting-artifacts` | [discover_provider_artifacts()](../backend/app/api_meetings.py) |
| DELETE | `/api/connections/{rid}` | [disconnect()](../backend/app/api_connections.py) |
| PATCH | `/api/connections/{rid}` | [update_connection()](../backend/app/api_connections.py) |
| GET | `/api/connections/{rid}/drive/files` | [drive_files()](../backend/app/api_connections.py) |
| POST | `/api/connections/{rid}/drive/import` | [drive_import()](../backend/app/api_connections.py) |
| GET | `/api/connections/{rid}/resources` | [resources()](../backend/app/api_connections.py) |
| POST | `/api/connections/{rid}/series` | [series()](../backend/app/api_connections.py) |
| POST | `/api/connections/{rid}/sync` | [sync()](../backend/app/api_connections.py) |
| GET | `/api/conversations` | [conversations()](../backend/app/main.py) |
| POST | `/api/conversations` | [create_conversation()](../backend/app/main.py) |
| POST | `/api/conversations/{conversation_id}/context-preview` | [preview()](../backend/app/api_agents.py) |
| GET | `/api/conversations/{conversation_id}/runs` | [list_runs()](../backend/app/api_agents.py) |
| POST | `/api/conversations/{conversation_id}/runs` | [create_run()](../backend/app/api_agents.py) |
| GET | `/api/conversations/{rid}/context` | [context_preview()](../backend/app/api_knowledge.py) |
| GET | `/api/conversations/{rid}/messages` | [messages()](../backend/app/main.py) |
| POST | `/api/conversations/{rid}/messages` | [send_message()](../backend/app/main.py) |
| GET | `/api/deliveries` | [deliveries()](../backend/app/api_connections.py) |
| POST | `/api/deliveries` | [enqueue()](../backend/app/api_connections.py) |
| POST | `/api/deliveries/{rid}/undo` | [undo_delivery()](../backend/app/api_connections.py) |
| GET | `/api/directory` | [directory()](../backend/app/main.py) |
| GET | `/api/documents` | [documents()](../backend/app/main.py) |
| POST | `/api/documents` | [upload_document()](../backend/app/main.py) |
| DELETE | `/api/documents/{rid}` | [delete_document()](../backend/app/main.py) |
| GET | `/api/documents/{rid}` | [document()](../backend/app/main.py) |
| GET | `/api/documents/{rid}/index` | [document_index()](../backend/app/api_knowledge.py) |
| POST | `/api/documents/{rid}/index/retry` | [retry_index()](../backend/app/api_knowledge.py) |
| GET | `/api/documents/{rid}/original` | [download_original()](../backend/app/api_knowledge.py) |
| GET | `/api/documents/{rid}/revisions` | [document_revisions()](../backend/app/api_knowledge.py) |
| POST | `/api/documents/{rid}/revisions` | [upload_revision()](../backend/app/api_knowledge.py) |
| GET | `/api/health/live` | [live()](../backend/app/main.py) |
| GET | `/api/health/ready` | [ready()](../backend/app/main.py) |
| GET | `/api/hierarchy-labels` | [hierarchy_labels()](../backend/app/api_governance.py) |
| GET | `/api/incidents` | [incidents()](../backend/app/api_operations.py) |
| POST | `/api/incidents` | [create_incident()](../backend/app/api_operations.py) |
| POST | `/api/incidents/{rid}/actions` | [incident_action()](../backend/app/api_operations.py) |
| POST | `/api/job-drafts` | [draft_job()](../backend/app/api_job_drafts.py) |
| GET | `/api/jobs` | [jobs()](../backend/app/api_operations.py) |
| POST | `/api/jobs` | [create_job()](../backend/app/api_operations.py) |
| GET | `/api/jobs/templates` | [templates()](../backend/app/api_governance.py) |
| PATCH | `/api/jobs/{rid}` | [update_job()](../backend/app/api_operations.py) |
| POST | `/api/jobs/{rid}/approve` | [approve()](../backend/app/api_governance.py) |
| PUT | `/api/jobs/{rid}/definition` | [edit_job()](../backend/app/api_governance.py) |
| POST | `/api/jobs/{rid}/reject` | [reject()](../backend/app/api_governance.py) |
| POST | `/api/jobs/{rid}/run` | [run_job()](../backend/app/api_operations.py) |
| GET | `/api/jobs/{rid}/runs` | [job_runs()](../backend/app/api_operations.py) |
| POST | `/api/jobs/{rid}/submit` | [submit()](../backend/app/api_governance.py) |
| POST | `/api/jobs/{rid}/undo-approval` | [undo_approval()](../backend/app/api_governance.py) |
| GET | `/api/meetings` | [meetings()](../backend/app/api_meetings.py) |
| POST | `/api/meetings` | [create()](../backend/app/api_meetings.py) |
| GET | `/api/meetings/people` | [meeting_people()](../backend/app/api_meetings.py) |
| GET | `/api/meetings/{rid}` | [meeting()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/accept` | [accept()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/archive` | [archive()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/artifacts` | [import_artifact()](../backend/app/api_meetings.py) |
| GET | `/api/meetings/{rid}/artifacts/{artifact_id}` | [artifact_content()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/draft` | [draft()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/extraction/cancel` | [cancel_extraction()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/provider-artifacts` | [provider_artifact()](../backend/app/api_meetings.py) |
| POST | `/api/meetings/{rid}/publish` | [publish()](../backend/app/api_meetings.py) |
| PUT | `/api/meetings/{rid}/review` | [review()](../backend/app/api_meetings.py) |
| GET | `/api/memories` | [memories()](../backend/app/main.py) |
| POST | `/api/memories` | [create_memory()](../backend/app/main.py) |
| DELETE | `/api/memories/{rid}` | [forget_memory()](../backend/app/main.py) |
| PATCH | `/api/memories/{rid}` | [update_memory()](../backend/app/main.py) |
| GET | `/api/notes` | [notes()](../backend/app/main.py) |
| POST | `/api/notes` | [create_note()](../backend/app/main.py) |
| PATCH | `/api/notes/{rid}` | [update_note()](../backend/app/main.py) |
| GET | `/api/notifications` | [notifications()](../backend/app/api_operations.py) |
| POST | `/api/notifications/{rid}/read` | [read_notification()](../backend/app/api_operations.py) |
| GET | `/api/personal/actions` | [actions()](../backend/app/api_personal.py) |
| POST | `/api/personal/actions/{rid}/undo` | [undo()](../backend/app/api_personal.py) |
| GET | `/api/personal/captures` | [captures()](../backend/app/api_personal.py) |
| POST | `/api/personal/captures` | [capture()](../backend/app/api_personal.py) |
| GET | `/api/personal/captures/{rid}` | [get_capture()](../backend/app/api_personal.py) |
| POST | `/api/personal/captures/{rid}/apply` | [apply_capture()](../backend/app/api_personal.py) |
| POST | `/api/personal/captures/{rid}/classify` | [classify()](../backend/app/api_personal.py) |
| GET | `/api/personal/checkins` | [checkins()](../backend/app/api_personal.py) |
| POST | `/api/personal/checkins` | [start_checkin()](../backend/app/api_personal.py) |
| POST | `/api/personal/checkins/{rid}/complete` | [complete_checkin()](../backend/app/api_personal.py) |
| GET | `/api/personal/folders` | [folders()](../backend/app/api_personal.py) |
| POST | `/api/personal/folders` | [create_folder()](../backend/app/api_personal.py) |
| GET | `/api/personal/organization` | [organization()](../backend/app/api_personal.py) |
| POST | `/api/personal/organization/preview` | [organization_preview()](../backend/app/api_personal.py) |
| POST | `/api/personal/organization/{rid}/apply` | [organization_apply()](../backend/app/api_personal.py) |
| POST | `/api/personal/organization/{rid}/undo` | [organization_undo()](../backend/app/api_personal.py) |
| GET | `/api/personal/placements` | [placements()](../backend/app/api_personal.py) |
| GET | `/api/personal/settings` | [settings()](../backend/app/api_personal.py) |
| PATCH | `/api/personal/settings` | [update_settings()](../backend/app/api_personal.py) |
| GET | `/api/personal/tasks` | [tasks()](../backend/app/api_personal.py) |
| PATCH | `/api/personal/tasks/{rid}` | [update_task()](../backend/app/api_personal.py) |
| GET | `/api/preferences` | [preferences()](../backend/app/api_knowledge.py) |
| PATCH | `/api/preferences` | [update_preferences()](../backend/app/api_knowledge.py) |
| GET | `/api/projects` | [projects()](../backend/app/main.py) |
| POST | `/api/projects` | [create_project()](../backend/app/main.py) |
| PATCH | `/api/projects/{rid}` | [update_project()](../backend/app/main.py) |
| GET | `/api/publications` | [publications()](../backend/app/api_operations.py) |
| GET | `/api/reminders` | [reminders()](../backend/app/main.py) |
| POST | `/api/reminders` | [create_reminder()](../backend/app/main.py) |
| PATCH | `/api/reminders/{rid}` | [update_reminder()](../backend/app/main.py) |
| GET | `/api/reports` | [reports()](../backend/app/api_operations.py) |
| POST | `/api/setup` | [setup()](../backend/app/api_governance.py) |
| GET | `/api/setup/status` | [setup_status()](../backend/app/api_governance.py) |
| POST | `/api/speech/transcribe` | [transcribe()](../backend/app/api_connections.py) |
| GET | `/api/subscriptions` | [subscriptions()](../backend/app/api_operations.py) |
| PUT | `/api/subscriptions` | [update_subscriptions()](../backend/app/api_operations.py) |
| GET | `/api/workspaces` | [workspaces()](../backend/app/main.py) |
| POST | `/api/workspaces` | [create_workspace()](../backend/app/main.py) |
| PATCH | `/api/workspaces/{rid}` | [update_workspace()](../backend/app/main.py) |
| GET | `/api/workspaces/{rid}/brief` | [get_brief()](../backend/app/api_knowledge.py) |
| POST | `/api/workspaces/{rid}/brief` | [draft_brief()](../backend/app/api_knowledge.py) |
| POST | `/api/workspaces/{rid}/brief/generate` | [generate_brief()](../backend/app/api_knowledge.py) |
| POST | `/api/workspaces/{rid}/brief/{brief_id}/publish` | [publish_brief()](../backend/app/api_knowledge.py) |
| GET | `/api/workspaces/{rid}/members` | [members()](../backend/app/main.py) |
| POST | `/api/workspaces/{rid}/members` | [add_member()](../backend/app/main.py) |
| DELETE | `/api/workspaces/{rid}/members/{user_id}` | [remove_member()](../backend/app/main.py) |
| GET | `/{path:path}` | [frontend()](../backend/app/main.py) |
