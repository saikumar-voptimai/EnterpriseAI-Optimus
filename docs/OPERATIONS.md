# Configuration and operation

Run commands from the repository root. `scripts/compose.sh` loads `.env` through Docker Compose. Do not source `.env` in a shell, post expanded `docker compose config`, or share secrets and backup contents in support messages.

## Configuration

`scripts/start.sh` creates required random secrets and prompts for OpenRouter. `.env.example` is the configuration reference. Changes take effect after recreating affected services:

```bash
./scripts/compose.sh up -d --force-recreate api scheduler executor connectors meetings
```

| Settings | Purpose |
| --- | --- |
| `APP_ORIGIN`, `SESSION_COOKIE_SECURE` | Exact browser origin and cookie transport; Tailscale setup sets HTTPS automatically |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Compose database connection; generated password is URI-safe hexadecimal |
| `BOOTSTRAP_TOKEN` | First-run administrator creation; unusable after initialization |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key for saved connector secrets; preserve across upgrades/restores |
| `ALLOW_EXTERNAL_AI`, `OPENROUTER_API_KEY` | Instance-level permission and credential for remote AI |
| `OPENROUTER_MODELS`, `OPENROUTER_DEFAULT_MODEL` | Allowed exact model IDs and default; choose compatible tool/structured-output models |
| `EMBEDDINGS_ENABLED`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | Semantic retrieval profile; changing the profile requires reindexing affected revisions |
| `AGENT_MAX_MODEL_STEPS`, `AGENT_MAX_TOOL_CALLS` | Bound individual agent runs |
| `AGENT_LEASE_SECONDS`, `AGENT_MAX_ATTEMPTS` | Durable run ownership and bounded recovery |
| `WORKER_POLL_SECONDS`, `JOB_LEASE_SECONDS` | Scheduling poll and job lease settings |
| `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID` | Outlook/Graph OAuth application registration |
| `SPEECH_API_KEY`, `SPEECH_BASE_URL`, `SPEECH_MODEL` | Optional OpenAI-compatible transcription; default uses Groq's hosted Whisper endpoint |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` | Optional enterprise email transport |
| `MAX_UPLOAD_BYTES` | Per-upload maximum, including bounded extraction; increase only after measuring memory |

The selected model and speech IDs are deployment defaults, not availability or price guarantees. Verify them in the provider account. Document embeddings and voice transcription are external processing too; an on-premises app does not make cloud inference offline.

## Processes and data

- `api` serves compiled Svelte and `/api` on one origin.
- `scheduler` performs due-work orchestration and personal routines.
- `executor` executes agent work.
- `connectors` synchronizes configured sources and dispatches external deliveries.
- `meetings` processes durable meeting-minutes extraction work.
- `migrate` applies Alembic once before application processes start.
- `db` is PostgreSQL 16 with pgvector, with no host-published port.

`postgres_data` stores application tables, graph checkpoints, originals/revisions and embeddings. `application_data` provides writable staging/storage for application components. Root filesystems are read-only. The tokenizer is downloaded into the image at build time, so document chunking does not fetch it at runtime.

Do not delete named volumes during routine restarts. Do not change database major version by merely changing the image tag. Use an explicit PostgreSQL major-version upgrade procedure.

## Connections

**Microsoft:** create a web application in the permitted Microsoft tenant, supply the configured ID/secret, and register `{APP_ORIGIN}/api/connections/microsoft/callback` as the exact web callback displayed in Connections. Each employee connects their own calendar. Optional transcript access requires its own permissions and actual artifact availability. Tailscale users must be connected when their browser follows the OAuth callback. The private Jetson polls outbound; it has no public provider webhook listener.

**InfluxDB:** use a read-only credential for the intended organization/buckets. Test connectivity, enumerate resources, select a bucket and meaningful measurement/field/tag filters, and retain units and timezone context in the Workspace Brief. The connector accepts structured reads; it does not provide an arbitrary query console or industrial write tool.

**Meetings:** supplied minutes or a transcript can be attached without calendar access. Provider-native imports require access to that exact meeting occurrence and available cloud artifacts. A meeting URL alone is insufficient. Review AI-extracted decisions, owners and due dates before distribution.

**Notifications:** configure enterprise SMTP for email and Teams Workflows for a channel webhook. These are separate from mailbox-reading or calendar OAuth. Notification links retain application access checks.

## Backup

```bash
./scripts/backup.sh
# Or choose a new destination:
./scripts/backup.sh /secure-backups/voptimai-before-upgrade.tar.gz
```

The script records the running application services, stops those writers, creates a custom-format PostgreSQL dump and file-volume archive, and resumes the original services even if backup fails. This causes a short maintenance window. The bundle contains `database.dump`, `files.tar.gz`, and a manifest with their hashes. A checksum sidecar protects the final bundle. Existing destinations are never overwritten.

A backup does **not** contain `.env`, Tailscale state, Docker images, or the optional Caddy CA. Keep a protected `.env` copy separately, especially the credential encryption key. Copy backups off the Jetson. Because document originals are transactional database records, the database dump preserves them; the file-volume archive also preserves any application staging files.

## Restore drill

Use a separate repository directory and a new `COMPOSE_PROJECT_NAME`, for example `voptimai-restore`. Generate fresh database credentials and use an unused `APP_PORT`. Restore the backed-up **credential encryption key** into this new `.env`; do not replace the fresh database password with one that does not match the new volume. Disable cloud AI and external transports during a drill.

Build the application image, then start only the new database:

```bash
./scripts/compose.sh build
./scripts/compose.sh up -d db
./scripts/restore.sh /secure-backups/voptimai-before-upgrade.tar.gz
./scripts/compose.sh run --rm migrate
```

The script validates the bundle, both internal hashes, archive paths/types, and a PostgreSQL archive listing. It refuses a destination containing database objects or application files, and refuses running application writers. Database restore is one transaction; file restore publishes only after extraction validation. There is no cross-filesystem/database transaction, so if the file stage fails after database restoration, investigate and repeat using another fresh project.

Start `api` first to inspect users, workspaces and representative documents. Do not start schedulers or connectors on a restored copy with real provider access until you have addressed active schedules and delivery state. A test restoration contains historical sessions and jobs; it is not a new demo installation.

## Upgrade

1. Retain the current release package and take a verified backup.
2. Restore to a disposable project and test the new migration/application revision.
3. Stop application writers, install the new source, and build.
4. Apply migrations and start services only after they succeed.

```bash
./scripts/compose.sh stop api scheduler executor connectors meetings
./scripts/compose.sh build
./scripts/compose.sh run --rm migrate
./scripts/compose.sh up -d api scheduler executor connectors meetings
./scripts/diagnose.sh
```

For upgrades from 1.0, run `python3 scripts/configure.py` once to add missing generated keys without replacing existing secrets. Review new `.env.example` options. The replacement `pgvector` image keeps PostgreSQL major version 16 and installs the extension required by the new migration. The migration owns schema changes; do not edit the initial migration or hand-create tables.

A migration rollback may require restoring the earlier backup with the previous application image. Running old code against a new schema is not a supported rollback method.

## Diagnostics

```bash
./scripts/diagnose.sh
./scripts/compose.sh logs --tail=100 migrate api scheduler executor connectors meetings
```

The health endpoints distinguish process liveness and database readiness. Check an individual run's status and evidence before retrying a failed AI request. Provider failures, unavailable transcripts, missing permissions and unsupported models should be resolved at their source. Do not repeatedly retry unknown external-delivery outcomes as if no message had been sent.

The host CLI remains available for account recovery:

```bash
./scripts/compose.sh run --rm api python -m app.cli reset-password --email employee@example.com
```

It prompts for the new password and revokes that user's sessions. Do not put passwords in command arguments. The first-run token is not a password reset mechanism.

## Native tests and release gates

Tests must use a disposable PostgreSQL database. Fixtures truncate application tables. The CI workflow starts native PostgreSQL 16/pgvector, applies migrations, executes backend and frontend checks, runs deployment-script tests, and defines AMD64/ARM64 image builds. Refer to [VALIDATION.md](VALIDATION.md) for which checks were actually executed for this package.
