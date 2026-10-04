# Running Optimus locally (Neon + pgvector)

This is the demo/evaluation setup: no Docker required, one managed PostgreSQL
database (Neon) holding all data including embeddings (pgvector), and three AI
model levels. All commands are bash. They run in Git Bash on Windows, and in
Linux or macOS terminals. The Jetson/Docker deployment in [JETSON.md](JETSON.md)
is unchanged.

| Component | Where it runs | Holds |
| --- | --- | --- |
| Web app + API (FastAPI, Svelte build) | `http://localhost:8088` on the laptop | Nothing; stateless |
| Workers (scheduler, executor, connectors, meetings) | Background process on the laptop | Nothing; stateless |
| Neon PostgreSQL + pgvector | Neon (London) | Users, chats and agent runs, documents and revisions, embeddings, jobs, incidents, meetings |
| AI models | Hosted service | Model and embedding calls |

## Prerequisites

- Python 3.12, Node.js 22 or newer, Git, curl.
- A Neon project. Neon includes pgvector; migrations enable it automatically.
- An API key for the AI service (`OPENROUTER_API_KEY`).

The `.venv` is platform-specific. If you switch between Git Bash and WSL, use a
separate clone for each.

## 1. One-time setup

```bash
./scripts/local/setup.sh
```

This creates `.venv`, installs backend packages, builds the frontend, and creates
`.env` from [`.env.local.example`](../.env.local.example) with a generated
first-run token and credential-encryption key. Back up `.env`; it is never committed.

## 2. Neon branches and `.env`

Use one Neon project with a branch per environment, for example `production`,
`development`, `demo` and `test`. Each branch has its own connection string. In the
Neon console choose *Connect* with **connection pooling off** (the host has no
`-pooler` suffix), then change `postgresql://` to `postgresql+psycopg://`:

```
DATABASE_URL=postgresql+psycopg://neondb_owner:PASSWORD@ep-xxxx.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
DATABASE_URL_NEON_DEMO=...
DATABASE_URL_NEON_DEVELOPMENT=...
DATABASE_URL_NEON_TEST=...
```

`DATABASE_URL` is what the app uses. Pooling stays off because the app sets a session
time zone when it connects, which Neon's pooled endpoint rejects.

**Pick a region close to where the laptop runs.** A chat answer makes about 170
small database round trips (the app re-checks permissions at every step): about one
second at 5 ms per round trip, about 30 seconds at 180 ms. Measure:

```bash
cd backend && ../.venv/Scripts/python -c "import time; from sqlalchemy import text; from app.db import SessionLocal; s = SessionLocal(); s.execute(text('select 1')); t = time.perf_counter(); [s.execute(text('select 1')) for _ in range(10)]; print(f'{(time.perf_counter()-t)*100:.0f} ms per round trip')"
```

Branches are copy-on-write: create `development`, `demo` and `test` from `production`
after its schema exists, and they inherit it. *Reset from parent* gives a branch a fresh
copy; deleting the `demo` branch removes the demo entirely.

**AI model levels.** Users choose *Fast*, *Medium Reasoning* or *High*; model names are
never shown in the interface or sent to the browser.

| Setting | Used for |
| --- | --- |
| `OPENROUTER_MODEL_FAST` | Quick answers, note classification, job drafting |
| `OPENROUTER_MODEL_MEDIUM` | Default for chat and scheduled analysis |
| `OPENROUTER_MODEL_HIGH` | Harder questions, selectable in chat and jobs |

Models must support tool calling and structured outputs.

Integrations (Google, Microsoft, Zoom, Slack, Teams, email) are set up as described in
[INTEGRATIONS.md](INTEGRATIONS.md).

## 3. Start and stop

```bash
./scripts/local/start.sh                      # uses DATABASE_URL (demo)
./scripts/local/start.sh --env development    # uses DATABASE_URL_NEON_DEVELOPMENT for this run
```

**Environments.** `APP_ENV` in `.env` (overridable with `--env`) selects the environment:
`DATABASE_URL_NEON_<ENV>` becomes `DATABASE_URL`, and any other `KEY_<ENV>` line becomes
`KEY` for that run (for example `GOOGLE_CLIENT_ID_DEMO`). The first output line lists what
was applied.

The script applies database migrations, starts the workers in the background and
runs the web app. Open `http://localhost:8088`. On a new branch, enter the printed
setup token once to create the administrator and enterprise, then follow the
[user guide](USER_GUIDE.md).

Press **Ctrl+C** to stop everything. If the terminal was closed instead, run
`./scripts/local/stop.sh`. Worker log: `tail -f .local/logs/worker.log`.

Options: `--no-worker` runs only the web app; `--skip-migrations` skips Alembic.

## Neon specifics

- **Compute hours.** The worker lanes poll the database (chat requests every 0.5 s,
  other work every `WORKER_POLL_SECONDS`), so a branch's compute stays awake while the
  app runs. Stop the app between sessions.
- **Data API lockdown.** If Neon's Data API is enabled, its `anonymous` and
  `authenticated` roles could reach tables over HTTP. Every migration run revokes their
  access and enables row-level security on all application tables. Optimus connects as
  the table owner and is unaffected.
- **Optional schema per environment.** `DATABASE_SCHEMA` keeps the app's tables in their
  own schema within one branch (`DROP SCHEMA <name> CASCADE;` removes them). With a
  branch per environment, leave it at `public`.

## Plant data

Add the connection in a workspace's *Connections* tab with a read-only token, open
*Settings* and tick the data sets the workspace may use. The organization is selected
automatically when the token can see only one. In chat, the assistant lists the
connection's data sets, discovers measurement and field names and the latest reading
time, then reads bounded windows (at most 32 days, aggregated for long ranges). Ask,
for example: *"What measurements are in bf2_evonith_raw, and what was the average
hearth cooling-water flow over the last 24 hours?"*

## How semantic search works

Documents are split into chunks and embedded by the connector worker; a document's
index status turns *ready* when done. Searches combine full-text and pgvector cosine
similarity (HNSW index) in one SQL query that also applies the caller's workspace,
classification and external-AI permissions, so a similarity match can never surface a
document the user may not read. An optional Qdrant backend (`VECTOR_BACKEND=qdrant`,
`setup.sh --with-qdrant`) is available but not needed.

## Tests

Tests **truncate every table**. Run them only against the `test` branch or a local
container, never `demo`, `development` or `production`:

```bash
export PYTHONUTF8=1 PYTHONPATH=backend
export TEST_DATABASE_URL="$(sed -n 's/^DATABASE_URL_NEON_TEST=//p' .env)"
.venv/Scripts/python -m pytest -q backend/tests
```

With Docker instead:

```bash
docker run -d --name optimus-test-db -e POSTGRES_USER=optimus -e POSTGRES_PASSWORD=optimus-test \
  -e POSTGRES_DB=optimus_test -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.6-pg16-bookworm
export TEST_DATABASE_URL=postgresql+psycopg://optimus:optimus-test@127.0.0.1:55432/optimus_test
```
