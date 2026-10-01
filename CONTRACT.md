# Implementation contract — V-OptimAIse 2.0

This contract describes the deployable application's boundaries. The complete route/method locator is [docs/CODE_MAP.md](docs/CODE_MAP.md); request/response schemas are available from FastAPI `/api/openapi.json` on the running app.

## Supported deployment

One enterprise, one PostgreSQL 16 database with pgvector, Svelte assets served by FastAPI, CPU containers on AMD64/ARM64. The initial target is Jetson Orin Nano 8 GB with private Tailscale HTTPS and remote OpenRouter inference. Root filesystem is read-only; database and application data use named volumes. No Docker/GPU runtime or physical Jetson performance claim is implied by source code or CI configuration alone.

`./scripts/start.sh --tailscale` creates missing protected configuration, prompts for OpenRouter, applies migrations and starts API/scheduler/executor/connectors/meetings. `--local` uses loopback HTTP. The first-run setup endpoint requires a generated bootstrap token and valid same-origin request, creates the initial administrator and enterprise records transactionally, and closes once initialization has occurred.

## State authority

PostgreSQL is authoritative for identities, scope hierarchy, workspace roles, preferences, knowledge revisions, chat, jobs, runs, checkpoints, connector state, meetings and deliveries. There is no JSON-file production scheduler or process-memory-only reminder store. Domain records remain authoritative over LangGraph checkpoints. Original document bytes are retained for new revisions in the database; earlier text-only documents cannot recover missing originals.

Migrations are additive revisions after `0001_initial`; do not edit that initial migration for new releases. `docs/schema.sql` is a compiled reference, not the installation mechanism. Backups preserve database plus the application file volume. The separately protected encryption key is required to recover encrypted credentials.

## Identity and authorization

- Password hashes use Argon2; cookie sessions are persisted, revocable, HTTP-only and same-site.
- Unsafe browser operations enforce the canonical origin and CSRF contract after login. Tailscale reachability is not application authorization.
- Workspace membership, classification, scope grants and explicit delegation determine access. Hierarchy names are configurable labels, not inferred permissions.
- Private content remains owner-specific; an administrator role is not a blanket permission to read every private note.
- Tool execution, retrieval, connector use and final answer persistence recheck their resource authority.
- Workspace briefs and skill instructions cannot grant privileges. Connector credentials are encrypted and never sent as graph/tool context.

## Agent execution

The shared LangGraph runtime loads brief/preferences/skills and authorized context, invokes a bounded model/tool loop, validates consumed sources, and finalizes under a durable lease. Chat enqueue uses a client request ID for retry idempotency. Progress and cancellation are explicit run state. Checkpoint writes validate active execution ownership. Only server-defined tools are exposed; there is no arbitrary code execution skill loader.

Scheduled analytical jobs reuse the same graph under their job lease. Their retry boundary is the durable job execution, not a claim of independent checkpoint recovery at every graph node. Model/provider capability must match the selected tools and structured outputs.

## Retrieval

Documents use revisioned originals, extraction, token chunks and explicit embedding profile/state. Lexical retrieval remains available while approved semantic indexing is pending. SQL audience filters and service access checks restrict candidates/results; similarity never overrides authorization. Vectors are stored in PostgreSQL. `VECTOR_BACKEND=pgvector` (default) ranks them with pgvector; `VECTOR_BACKEND=qdrant` ranks them in a Qdrant collection whose candidates are re-authorized through the same SQL audience filter before use, so Qdrant can be rebuilt from PostgreSQL (`knowledge_cli qdrant-sync`) and never widens access. Graph databases are not required.

## Personal assistant

Captures save their original note before interpretation. Classification can propose linked tasks/reminders/memories and filing. Applying accepted internal changes records an idempotent action with conflict-aware Undo. Folder reorganization acts on references rather than rewriting originals. Daily check-ins create an in-app prompt; weekly organization is configured as disabled, suggestion or automatic where offered. Active browser microphone capture uses an optional hosted transcription key, not always-on recording.

## Enterprise jobs and findings

Employees draft analytical schedules; authorized managers approve material revisions. Team automation uses constrained workspace authority and an accountable owner. Validation results are typed data-quality outcomes, not inferred from model completion status. Deterministic operational reporting defines metric bounds, evaluation windows and aggregation. An anomaly probability requires a real calibrated algorithm and is not invented by the assistant.

## Connections and meetings

Implemented provider boundaries are Outlook calendar/Teams transcript reads via Microsoft Graph, Zoom completed cloud transcript imports, InfluxDB v2 bounded reads, configured hosted transcription, SMTP and Teams Workflows notification delivery. Each needs its own credentials/permissions/reachable resources. Direct PLC/SCADA/OPC UA/Modbus, Google Meet, general mailbox ingestion and recording bots are not implemented.

Microsoft redirect path is `/api/connections/microsoft/callback`. Calendar read and transcript read are separate permissions. Private tailnet hosting uses outbound polling. InfluxDB uses a read-only token selected by the operator; software read methods are not a physical data diode.

Meeting Rooms contain participants and evidence. Durable draft extraction produces reviewable structured minutes. Publication checks destination workspace authority; personal changes require attendee acceptance. Archival retains approved durable results. Unknown owners/deadlines remain unresolved instead of invented.

## Outgoing delivery

An outbox record owns a delivery request and a server-enforced cancellation deadline. Pending deliveries can be cancelled during the 20-second grace window. Dispatcher claims and cancellation cannot both win the same pending row transition. Once a provider might have accepted a message, an unknown outcome is not blindly retried. A successful network response is not proof a human read the message. External message recall is not promised.

## Verification limits

Native PostgreSQL integration, concurrent-worker behavior, ARM64 image builds, backups on Docker volumes and physical Jetson acceptance are explicit gates. Local PGlite checks exercise SQLAlchemy/psycopg and actual migrated PostgreSQL-compatible schema but cannot establish native process locking/concurrency or Jetson behavior. See [docs/VALIDATION.md](docs/VALIDATION.md) for the executed record.
