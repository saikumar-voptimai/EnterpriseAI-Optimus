#!/usr/bin/env bash
# Avoid dumping expanded Compose configuration or environment secrets.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
echo 'Host architecture:'
uname -m
echo 'Docker and Compose versions:'
docker --version
docker compose version
echo 'Service state:'
"$task_root/scripts/compose.sh" ps -a
echo 'Database readiness:'
"$task_root/scripts/compose.sh" exec -T db sh -c \
  'exec pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
echo 'Application liveness and readiness:'
"$task_root/scripts/compose.sh" exec -T api python -c \
  'import urllib.request; [print(p, urllib.request.urlopen("http://127.0.0.1:8080/api/health/" + p, timeout=5).read().decode()) for p in ("live", "ready")]'
echo 'Database migration revision:'
"$task_root/scripts/compose.sh" exec -T api alembic current
echo 'Filesystem capacity:'
df -h "$task_root"
echo 'Review scheduler, executor, connectors and meetings logs and run history if work is delayed.'
