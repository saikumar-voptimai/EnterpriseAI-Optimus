#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
if [[ ! -f .env ]]; then
  echo 'Missing .env. Run ./scripts/init-env.sh first.' >&2
  exit 1
fi
exec docker compose "$@"
