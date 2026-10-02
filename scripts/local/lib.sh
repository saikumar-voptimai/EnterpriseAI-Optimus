# Shared helpers for scripts/local/*.sh. Sourced, not executed.
# Works in Git Bash (Windows), Linux and macOS.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_DIR="$ROOT/.local"
LOG_DIR="$LOCAL_DIR/logs"
PID_FILE="$LOCAL_DIR/pids"
ENV_FILE="$ROOT/.env"
QDRANT_VERSION="v1.19.1"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) PLATFORM=windows ;;
  Darwin) PLATFORM=macos ;;
  *) PLATFORM=linux ;;
esac

if [ "$PLATFORM" = windows ]; then
  VENV_PY="$ROOT/.venv/Scripts/python.exe"
  QDRANT_BIN="$LOCAL_DIR/qdrant/qdrant.exe"
else
  VENV_PY="$ROOT/.venv/bin/python"
  QDRANT_BIN="$LOCAL_DIR/qdrant/qdrant"
fi

# Windows Python otherwise defaults to the cp1252 code page.
export PYTHONUTF8=1 PYTHONUNBUFFERED=1

die() {
  echo "error: $*" >&2
  exit 1
}

# Print the value of KEY from .env without executing the file as shell code.
env_value() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1 | tr -d '\r' |
    sed 's/[[:space:]]*$//'
}

# Echo a command that runs Python 3.12, or fail.
find_python312() {
  local candidate
  for candidate in "py -3.12" python3.12 python3 python; do
    # shellcheck disable=SC2086  # "py -3.12" is intentionally two words.
    if $candidate -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

qdrant_asset() {
  case "$PLATFORM-$(uname -m)" in
    windows-*) echo "qdrant-x86_64-pc-windows-msvc.zip" ;;
    linux-x86_64) echo "qdrant-x86_64-unknown-linux-musl.tar.gz" ;;
    linux-aarch64 | linux-arm64) echo "qdrant-aarch64-unknown-linux-musl.tar.gz" ;;
    macos-arm64) echo "qdrant-aarch64-apple-darwin.tar.gz" ;;
    macos-x86_64) echo "qdrant-x86_64-apple-darwin.tar.gz" ;;
    *) return 1 ;;
  esac
}
