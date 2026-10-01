# V-OptimAIse 2.0

This release evolves the original single-enterprise application into a personal assistant and governed workspace platform. See README.md for startup and docs/USER_GUIDE.md for the product walkthrough.

## Changes

- Shared bounded LangGraph execution, PostgreSQL run state, fenced chat checkpoints, cancellation, progress and source validation.
- Deterministic workspace briefs and preferences; curated skills and typed tools; hybrid PostgreSQL full-text/pgvector retrieval with versioned originals and chunks.
- Personal text/voice capture, validated classification, projects and folders, reminders, daily check-ins, weekly organization and conflict-aware Undo.
- Explicit personal-chat note/reminder tools, with idempotent receipts and no shared-workspace access to private actions.
- Read-only InfluxDB v2, Microsoft calendar/Teams artifacts, Zoom transcripts, hosted speech and encrypted connection credentials.
- Durable chunked meeting extraction, evidence review, workspace publication and attendee acceptance.
- Natural-language job drafts, manager-approved immutable revisions, noninteractive workspace identities, exact temporal dependencies and DST-aware schedules.
- Deterministic data validation, incident-aware shift handovers, normalized production comparisons, and configured consecutive-failure escalation rules.
- Single delivery outbox for email/Teams with a server-enforced 20-second cancellation window and explicit uncertain-send handling.
- Guided setup, responsive mobile UI, administration/delegations, report cards, Docker worker roles, Tailscale setup, backup/restore and source-derived technical references.

## Upgrade

1. Back up the current installation and retain its credential-encryption key.
2. Update the source, retain the existing .env, and use scripts/start.sh with the deployment's existing access mode.
3. Migrations preserve existing owner-bound jobs as legacy. Submit/review a job to adopt governed workspace execution.
4. Existing documents can be upgraded with `python -m app.knowledge_cli backfill`. Retained text can be indexed; missing original files cannot be reconstructed.

Run Now graphs and scheduled dependency joins are explicitly distinguished in migration 0004. Checkpoint retention defaults to seven days for terminal execution payloads; conversations and final results remain.

## Verification and limits

148 backend tests, 6 deployment helper tests and 15 browser workflows passed. Svelte validation and production build passed. Tests used a disposable PGlite/PostgreSQL-compatible harness and controlled provider responses. Native PostgreSQL/ARM64 image builds are defined in CI; physical Jetson execution, native concurrent-session behavior and real provider accounts still require deployment acceptance. See docs/VALIDATION.md for the exact evidence and limits.
