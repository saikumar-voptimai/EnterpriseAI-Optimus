# Running Optimus locally (Supabase + pgvector + OpenRouter)

This is the demo/evaluation setup: no Docker required, one managed PostgreSQL
database holding all data including embeddings (pgvector), and three OpenRouter
model levels. All commands are bash. They run in Git Bash on Windows, and in
Linux or macOS terminals. The Jetson/Docker deployment in [JETSON.md](JETSON.md)
is unchanged.

| Component | Where it runs | Holds |
| --- | --- | --- |
| Web app + API (FastAPI, Svelte build) | `http://localhost:8088` on the laptop | Nothing; stateless |
| Workers (scheduler, executor, connectors, meetings) | Background process on the laptop | Nothing; stateless |
| PostgreSQL + pgvector (Supabase, or Neon) | Cloud | Users, chats and agent runs, documents and revisions, embeddings, jobs, incidents, meetings |
| OpenRouter | Cloud | Model and embedding calls |

## Prerequisites

- Python 3.12, Node.js 22 or newer, Git, curl.
- A Supabase project (free tier is fine) or a Neon project. Both ship pgvector;
  migrations enable it automatically.
- An OpenRouter API key with credit.

The `.venv` is platform-specific. If you switch between Git Bash and WSL, use a
separate clone for each.

## 1. One-time setup

```bash
./scripts/local/setup.sh
```

This creates `.venv`, installs backend packages, builds the frontend, and creates
`.env` from [`.env.local.example`](../.env.local.example) with a generated
first-run token and credential-encryption key. Back up `.env`; it is never committed.

Options: `--skip-frontend`, `--with-qdrant` (see [Optional: Qdrant](#optional-qdrant)).

## 2. Fill in `.env`

**`DATABASE_URL`**, using the `postgresql+psycopg://` scheme. Paste the provider's
string, then change `postgresql://` to `postgresql+psycopg://`:

- **Supabase:** *Connect* → **Session pooler**. The direct `db.<ref>.supabase.co` host
  is IPv6-only on the free plan; the session pooler works over IPv4. Do not use the
  transaction pooler (port 6543): it drops session settings and prepared statements.

  ```
  DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:PASSWORD@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
  ```

- **Neon:** *Connect* with pooling **off** (the host has no `-pooler` suffix).
  Neon's pooled endpoint rejects the session time-zone option the app sets at connect.

  ```
  DATABASE_URL=postgresql+psycopg://neondb_owner:PASSWORD@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
  ```

Pick the region closest to you; every page load makes several database round trips.

**`OPENROUTER_API_KEY`**: your key. The model levels are preset:

| Setting | Default | Used for |
| --- | --- | --- |
| `OPENROUTER_MODEL_FAST` | `anthropic/claude-haiku-4.5` | Note classification, job drafting |
| `OPENROUTER_MODEL_STANDARD` | `anthropic/claude-sonnet-5.5` | Default for chat and scheduled analysis |
| `OPENROUTER_MODEL_DEEP` | `anthropic/claude-opus-5.5` | Selectable in chat and jobs for harder questions |

Any OpenRouter model works if it supports tool calling and structured outputs.
Chat and job forms show the levels as *Fast / Standard / Deep*.

## 3. Start and stop

```bash
./scripts/local/start.sh
```

The script applies database migrations, starts the workers in the background and
runs the web app. Open `http://localhost:8088`, enter the printed setup token once
to create the administrator and enterprise, then follow the [user guide](USER_GUIDE.md).

Press **Ctrl+C** to stop everything. If the terminal was closed instead, run
`./scripts/local/stop.sh`. Worker log: `tail -f .local/logs/worker.log`.

Options: `--no-worker` runs only the web app; `--skip-migrations` skips Alembic.
Any `.env` setting can be overridden for one run from the shell, for example
`DATABASE_URL=... ./scripts/local/start.sh`.

## Supabase specifics

- **Data API lockdown.** Supabase publishes the `public` schema through its Data API
  to the `anon`/`authenticated` roles, and the anon key is public by design. Every
  migration run therefore revokes those roles' access and enables row-level security
  on all application tables. Optimus connects as the table owner and is unaffected.
  Supabase's Security Advisor should show no "RLS disabled" warnings for these tables.
  Optimus does not use the Data API, so you can also switch it off under
  *Project Settings → Data API*.
- **Free-plan limits:** 500 MB database, and projects pause after a week without
  activity. A paused project needs *Restore* in the dashboard before the app can
  connect. Uploaded originals are stored in the database; keep demo files small.
- **Connections:** the API and worker each keep a small connection pool through
  the session pooler. If you see "max clients reached", stop other clients
  (SQL editor tabs, other app instances).

## Neon specifics

The worker lanes poll the database every `WORKER_POLL_SECONDS` (default 5), so a
Neon compute stays awake while the app runs. Stop the app between demo sessions,
or raise the interval if compute hours matter more than responsiveness. Neon
branches make good disposable test databases (see [Tests](#tests)).

## How semantic search works

Documents are split into chunks and embedded through OpenRouter
(`openai/text-embedding-3-small`, 1536 dimensions) by the connector worker; a
document's index status turns *ready* when done. Searches combine full-text and
pgvector cosine similarity (HNSW index) in one SQL query that also applies the
caller's workspace, classification and external-AI permissions, so a similarity
match can never surface a document the user may not read.

## Optional: Qdrant

Set `VECTOR_BACKEND=qdrant` to rank similarity in Qdrant instead. PostgreSQL still
keeps every vector and re-authorizes each Qdrant hit. Run
`./scripts/local/setup.sh --with-qdrant` to download a local binary (the start
script then runs it), or set `QDRANT_URL`/`QDRANT_API_KEY` for Qdrant Cloud.
Fill an empty collection from PostgreSQL with
`cd backend && ../.venv/Scripts/python -m app.knowledge_cli qdrant-sync`
(`.venv/bin/python` on Linux/macOS).

## Tests

Tests **truncate every table**. Never point them at the database you demo from.
Use a disposable database, such as a separate Neon branch or a local container:

```bash
docker run -d --name optimus-test-db -e POSTGRES_USER=optimus -e POSTGRES_PASSWORD=optimus-test \
  -e POSTGRES_DB=optimus_test -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.6-pg16-bookworm

export PYTHONUTF8=1 PYTHONPATH=backend
export TEST_DATABASE_URL=postgresql+psycopg://optimus:optimus-test@127.0.0.1:55432/optimus_test
.venv/Scripts/python -m pytest -q backend/tests
```

Later sessions only need `docker start optimus-test-db`. To include the live
Qdrant test, also run a `qdrant/qdrant:v1.19.1` container on port 56333 and
`export TEST_QDRANT_URL=http://127.0.0.1:56333`.
