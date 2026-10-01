# Validation record

This file separates executed checks from deployment acceptance. Do not treat a test recipe as a completed remote CI run.

## Deployment checks executed in the authoring environment

- Python deployment helper tests: **6 passed**. Covered no-overwrite credential creation, literal environment handling, symlink rejection, file archive roundtrip, refusal to overwrite existing files, traversal/link rejection and duplicate-entry failure without partial file publication.
- Shell syntax checks passed for all `scripts/*.sh`.
- Base Compose, optional TLS overlay and CI YAML parsed successfully.
- First-run HTTP/database tests: **3 passed** against the migrated PGlite wire harness. Covered bootstrap-token rejection, origin/password validation, creation of a usable administrator/enterprise/workspace/session, and setup closure after initialization.
- Current ORM reference DDL and source route/class navigation were generated directly from source metadata/AST.

## Local database environment

The native environment has no Docker daemon/socket and maps only UID 0; PostgreSQL cannot be launched under its required non-root user here. Tests use a **disposable PGlite 0.5.8 / PostgreSQL 18.3 WASM instance with pgvector 0.8.1**, accessed by real psycopg/SQLAlchemy over a PostgreSQL-wire socket. It is a sequential functional harness, not native PostgreSQL 16.

The harness multiplexes a single backend. Tests that deliberately hold one database transaction while opening another can block and do not establish actual concurrent-session semantics. Native PostgreSQL tests remain required for leases, row locks, concurrency and process recovery. Product code must not be weakened to accommodate a WASM test limitation.

The Compose deployment pins `pgvector/pgvector:0.8.6-pg16-bookworm`. The official pgvector build publishes AMD64 and ARM64. The repository CI workflow defines native PostgreSQL/pgvector tests and multi-architecture builds; this authoring environment does not execute a Docker build or deploy to the user's Jetson.

## Integrated release checks

- Backend suite: **148 passed in 16.72 seconds**, including metadata parity, access isolation/revocation, actual LangGraph tool loops, checkpoint recovery, approval and Undo gates, temporal dependencies, DST scheduling, hybrid retrieval, incident findings, meeting extraction and provider protocol tests. [Recorded output](validation/backend-tests.txt).

- Svelte type/component check: **0 errors and 0 warnings**.
- Production frontend build passed; JavaScript bundle 271.57 kB (83.94 kB gzip), CSS 47.04 kB (10.31 kB gzip).
- Browser walkthrough: **15 workflows passed**, zero JavaScript errors and zero failed API requests. Included first-run setup, capture, preferences, folders, brief publication, documents, queued chat/cancellation, meetings, schedules, approvals/Undo, connections, administration, workspace creation and a 390px mobile viewport without horizontal overflow. See [machine-readable results](validation/browser-smoke.json).
- Alembic migrations exercised on fresh database and through upgrade → downgrade to v1 → upgrade. Metadata parity is checked by the backend suite.
- Python dependency compatibility check passed.

 Paid generation, embeddings, speech, Microsoft OAuth, Zoom artifacts, SMTP/Teams delivery and Influx connectivity require the pilot's real accounts and permitted endpoints. Tests use controlled provider responses and do not make paid calls.

## Device and account acceptance

1. Build the final image on the actual Orin Nano or run the remote ARM64 build gate.
2. Initialize a fresh installation using the documented one-command start.
3. Test login, microphone permission and Microsoft OAuth from an approved phone over Tailscale/mobile data.
4. Execute native PostgreSQL tests for multi-worker claims, stale-lease fencing, cancellation races and restart recovery.
5. Complete a database/file-volume backup and restore drill into a distinct project.
6. Verify each configured provider's actual scopes, resource availability, quotas and errors.
7. Run the engineer/plant-head/executive pilot against permitted data; inspect memory, queue delay, disk usage and model cost.

No simultaneous-user capacity, local LLM throughput, physical one-way PLC connectivity, SSO, HA or compliance certification is asserted by this package.
