#!/usr/bin/env bash
# One-time local setup for Optimus: Python 3.12 environment, frontend build,
# .env with generated secrets, and a local Qdrant binary.
#
#   ./scripts/local/setup.sh [--skip-frontend] [--skip-qdrant]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SKIP_FRONTEND=0
SKIP_QDRANT=0
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --skip-qdrant) SKIP_QDRANT=1 ;;
    -h | --help)
      sed -n '2,6p' "$0"
      exit 0
      ;;
    *) die "unknown option: $arg" ;;
  esac
done
mkdir -p "$LOCAL_DIR"

echo "== Python 3.12 environment"
if [ ! -x "$VENV_PY" ]; then
  PYTHON="$(find_python312)" || die "Python 3.12 is required (install it from python.org or your package manager)."
  # shellcheck disable=SC2086
  $PYTHON -m venv "$ROOT/.venv"
fi
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$ROOT/backend/requirements-dev.txt"

if [ "$SKIP_FRONTEND" = 0 ]; then
  echo "== Frontend build"
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
fi

echo "== Configuration"
if [ -f "$ENV_FILE" ]; then
  echo ".env already exists; left unchanged."
else
  token="$("$VENV_PY" -c 'import secrets; print(secrets.token_urlsafe(32))' | tr -d '\r')"
  key="$("$VENV_PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' | tr -d '\r')"
  (
    umask 077
    sed -e "s|^BOOTSTRAP_TOKEN=.*|BOOTSTRAP_TOKEN=$token|" \
      -e "s|^CREDENTIAL_ENCRYPTION_KEY=.*|CREDENTIAL_ENCRYPTION_KEY=$key|" \
      "$ROOT/.env.local.example" >"$ENV_FILE"
  )
  echo "Created .env with a setup token and credential key."
  echo "Back up CREDENTIAL_ENCRYPTION_KEY: saved connection passwords cannot be decrypted without it."
fi

if [ "$SKIP_QDRANT" = 0 ]; then
  echo "== Qdrant $QDRANT_VERSION"
  if [ -x "$QDRANT_BIN" ]; then
    echo "Qdrant binary already present."
  else
    asset="$(qdrant_asset)" || die "no Qdrant build for $PLATFORM/$(uname -m); run Qdrant yourself and set QDRANT_URL."
    mkdir -p "$LOCAL_DIR/qdrant"
    archive="$LOCAL_DIR/$asset"
    curl -fL --progress-bar -o "$archive" \
      "https://github.com/qdrant/qdrant/releases/download/$QDRANT_VERSION/$asset"
    case "$asset" in
      *.zip) "$VENV_PY" -m zipfile -e "$archive" "$LOCAL_DIR/qdrant" ;;
      *.tar.gz) tar -xzf "$archive" -C "$LOCAL_DIR/qdrant" ;;
    esac
    rm -f "$archive"
    chmod +x "$QDRANT_BIN" 2>/dev/null || true
    [ -x "$QDRANT_BIN" ] || die "the Qdrant binary was not found in $asset."
    echo "Installed $QDRANT_BIN"
  fi
fi

cat <<'EOF'

Setup complete. Before the first start, edit .env and set:
  DATABASE_URL        Neon direct connection string (postgresql+psycopg://...?sslmode=require)
  OPENROUTER_API_KEY  your OpenRouter key
Then run: ./scripts/local/start.sh
EOF
