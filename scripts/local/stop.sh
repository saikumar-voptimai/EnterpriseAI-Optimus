#!/usr/bin/env bash
# Stop Qdrant and workers left running by start.sh (for example after its
# terminal was closed instead of pressing Ctrl+C).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ ! -f "$PID_FILE" ]; then
  echo "Nothing recorded as running."
  exit 0
fi
while read -r pid; do
  if [ -n "$pid" ] && kill "$pid" 2>/dev/null; then
    echo "Stopped process $pid"
  fi
done <"$PID_FILE"
rm -f "$PID_FILE"
