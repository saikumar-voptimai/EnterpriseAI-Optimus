#!/usr/bin/env bash
# Start Optimus locally: Qdrant (when configured locally), database migrations,
# background workers and the web app at http://localhost:<APP_PORT>.
# Ctrl+C stops everything this script started.
#
#   ./scripts/local/start.sh [--env demo|development|...] [--no-worker] [--skip-migrations]
#
# --env NAME uses DATABASE_URL_NEON_<NAME> (or DATABASE_URL_<NAME>) from .env for this run.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NO_WORKER=0
SKIP_MIGRATIONS=0
TARGET_ENV=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-worker) NO_WORKER=1 ;;
    --skip-migrations) SKIP_MIGRATIONS=1 ;;
    --env)
      [ $# -ge 2 ] || die "--env needs a name, for example --env development"
      TARGET_ENV="$2"
      shift
      ;;
    -h | --help)
      sed -n '2,9p' "$0"
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

if [ -n "$TARGET_ENV" ]; then
  key="$(printf '%s' "$TARGET_ENV" | tr '[:lower:]-' '[:upper:]_')"
  url="$(env_value "DATABASE_URL_NEON_$key")"
  [ -n "$url" ] || url="$(env_value "DATABASE_URL_$key")"
  [ -n "$url" ] || die "no DATABASE_URL_NEON_$key or DATABASE_URL_$key in .env"
  export DATABASE_URL="$url"
  echo "Database: $TARGET_ENV branch"
fi

[ -x "$VENV_PY" ] || die "run ./scripts/local/setup.sh first."
[ -f "$ENV_FILE" ] || die "no .env found; run ./scripts/local/setup.sh first."
[ -f "$ROOT/frontend/dist/index.html" ] || die "the frontend is not built; run ./scripts/local/setup.sh."
[ -n "$(env_value DATABASE_URL)" ] ||
  die "set DATABASE_URL in .env (Neon direct connection string using postgresql+psycopg://)."
[ -n "$(env_value OPENROUTER_API_KEY)" ] ||
  echo "warning: OPENROUTER_API_KEY is empty; chat, classification and embeddings are unavailable." >&2
PORT="$(env_value APP_PORT)"
PORT="${PORT:-8088}"
mkdir -p "$LOG_DIR"

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
  rm -f "$PID_FILE"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

ready() { curl -fsS --max-time 2 "$1" >/dev/null 2>&1; }

if [ "$(env_value VECTOR_BACKEND)" = qdrant ]; then
  qdrant_url="$(env_value QDRANT_URL)"
  qdrant_url="${qdrant_url:-http://127.0.0.1:6333}"
  qdrant_url="${qdrant_url%/}"
  if ready "$qdrant_url/readyz"; then
    echo "Qdrant already running at $qdrant_url"
  elif [[ "$qdrant_url" =~ ^http://(127\.0\.0\.1|localhost):6333$ ]]; then
    [ -x "$QDRANT_BIN" ] || die "Qdrant is not installed; run ./scripts/local/setup.sh."
    (
      cd "$LOCAL_DIR/qdrant"
      export QDRANT__SERVICE__HOST=127.0.0.1 QDRANT__TELEMETRY_DISABLED=true
      export QDRANT__STORAGE__STORAGE_PATH=./storage QDRANT__STORAGE__SNAPSHOTS_PATH=./snapshots
      exec "$QDRANT_BIN"
    ) >"$LOG_DIR/qdrant.log" 2>&1 &
    qdrant_pid="$!"
    PIDS+=("$qdrant_pid")
    for _ in $(seq 60); do
      ready "$qdrant_url/readyz" && break
      kill -0 "$qdrant_pid" 2>/dev/null || die "Qdrant exited; see .local/logs/qdrant.log"
      sleep 0.5
    done
    ready "$qdrant_url/readyz" || die "Qdrant did not become ready; see .local/logs/qdrant.log"
    echo "Qdrant started at $qdrant_url (storage: .local/qdrant/storage)"
  else
    die "Qdrant at $qdrant_url is not reachable; check QDRANT_URL / QDRANT_API_KEY."
  fi
fi

cd "$ROOT/backend"
if [ "$SKIP_MIGRATIONS" = 0 ]; then
  echo "Applying database migrations..."
  "$VENV_PY" -m alembic upgrade head || die "migration failed; check DATABASE_URL and network access to Neon."
fi

if [ "$NO_WORKER" = 0 ]; then
  "$VENV_PY" -m app.worker --role all >>"$LOG_DIR/worker.log" 2>&1 &
  PIDS+=("$!")
  echo "Workers started (log: .local/logs/worker.log)"
fi
printf '%s\n' ${PIDS[@]+"${PIDS[@]}"} >"$PID_FILE"

echo
echo "Optimus: http://localhost:$PORT"
token="$(env_value BOOTSTRAP_TOKEN)"
[ -z "$token" ] || echo "First-run setup token: $token"
echo "Press Ctrl+C to stop."
echo
# Waiting on a background job keeps Ctrl+C responsive: the trap runs at once and
# stops every process, including native Windows processes that never see SIGINT.
"$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
PIDS+=("$!")
wait "$!"
