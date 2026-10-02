# Running Optimus locally (Neon + Qdrant + OpenRouter)

This is the demo/evaluation setup: no Docker required, data in a Neon PostgreSQL
project, semantic search in a local Qdrant, and three OpenRouter model levels.
All commands are bash. They run in Git Bash on Windows, and in Linux or macOS
terminals. The Jetson/Docker deployment in [JETSON.md](JETSON.md) is unchanged.

| Component | Where it runs | Holds |
| --- | --- | --- |
| Web app + API (FastAPI, Svelte build) | `http://localhost:8088` on the laptop | Nothing; stateless |
| Workers (scheduler, executor, connectors, meetings) | Background process on the laptop | Nothing; stateless |
| Neon PostgreSQL | Neon cloud | Users, chats and agent runs, documents and revisions, embeddings, jobs, incidents, meetings |
| Qdrant | `.local/qdrant` on the laptop (or Qdrant Cloud) | Similarity index rebuilt from PostgreSQL |
| OpenRouter | Cloud | Model and embedding calls |

## Prerequisites

- Python 3.12, Node.js 22 or newer, Git, curl.
- A Neon project. Neon includes pgvector; the migrations enable it automatically.
- An OpenRouter API key with credit.

The `.venv` and Qdrant binary are platform-specific. If you switch between Git Bash
and WSL, use a separate clone for each.

## 1. One-time setup

```bash
./scripts/local/setup.sh
```

This creates `.venv`, installs backend packages, builds the frontend, downloads
Qdrant into `.local/qdrant`, and creates `.env` from
[`.env.local.example`](../.env.local.example) with a generated first-run token and
credential-encryption key. Back up `.env`; it is never committed.

Options: `--skip-frontend`, `--skip-qdrant`.

## 2. Fill in `.env`

**`DATABASE_URL`**: in the Neon console choose *Connect*, turn **off** connection
pooling (the direct host has no `-pooler` suffix), copy the string and change
`postgresql://` to `postgresql+psycopg://`:

```
DATABASE_URL=postgresql+psycopg://neondb_owner:PASSWORD@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

Pick the Neon region closest to you; every page load makes several database round trips.
The direct endpoint is required because the app passes a session time-zone `options`
parameter when it connects, which Neon's pooled (PgBouncer) endpoint rejects.

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

The script starts Qdrant, applies migrations to Neon, starts the workers in the
background and runs the web app. Open `http://localhost:8088`, enter the printed
setup token once to create the administrator and enterprise, then follow the
[user guide](USER_GUIDE.md).

Press **Ctrl+C** to stop everything. If the terminal was closed instead, run
`./scripts/local/stop.sh`. Logs:

```bash
tail -f .local/logs/worker.log    # workers
tail -f .local/logs/qdrant.log    # Qdrant
```

Options: `--no-worker` runs only the web app; `--skip-migrations` skips Alembic.

## Data and vectors

- PostgreSQL (Neon) is the system of record, including each chunk's embedding.
  Qdrant only ranks candidates. Every candidate is re-checked against workspace
  membership, classification and external-AI permission in SQL before it is used,
  so Qdrant never widens access.
- Rebuild or refill Qdrant from Neon at any time (for example on a new laptop).
  Use `.venv/bin/python` instead of `.venv/Scripts/python` on Linux or macOS:

  ```bash
  cd backend && ../.venv/Scripts/python -m app.knowledge_cli qdrant-sync
  ```

- To move semantic search back into PostgreSQL later, set `VECTOR_BACKEND=pgvector`.
  The vectors are already there; nothing needs re-embedding.
- For Qdrant Cloud, set `QDRANT_URL` to the cluster URL and `QDRANT_API_KEY`;
  the start script then skips the local binary.

## Neon usage

The worker lanes poll the database every `WORKER_POLL_SECONDS` (default 5), so a
Neon compute stays awake while the app runs. Stop the app between demo sessions,
or raise the interval if compute hours matter more than responsiveness.

## Tests

Tests **truncate every table**. Never point them at the Neon database you demo
from. Use a disposable database, such as a separate Neon branch or a local container:

```bash
docker run -d --name optimus-test-db -e POSTGRES_USER=optimus -e POSTGRES_PASSWORD=optimus-test \
  -e POSTGRES_DB=optimus_test -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.6-pg16-bookworm
docker run -d --name optimus-test-qdrant -p 127.0.0.1:56333:6333 qdrant/qdrant:v1.19.1

export PYTHONUTF8=1 PYTHONPATH=backend
export TEST_DATABASE_URL=postgresql+psycopg://optimus:optimus-test@127.0.0.1:55432/optimus_test
export TEST_QDRANT_URL=http://127.0.0.1:56333   # optional; enables the live Qdrant test
.venv/Scripts/python -m pytest -q backend/tests
```

Later sessions only need `docker start optimus-test-db optimus-test-qdrant`.
