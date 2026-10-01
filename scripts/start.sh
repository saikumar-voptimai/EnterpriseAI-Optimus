#!/usr/bin/env bash
# One-command local or Tailscale installation; repeated runs preserve data/secrets.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
task_mode=()
task_ai=(--configure-ai)
for task_arg in "$@"; do
  case "$task_arg" in
    --tailscale) task_mode=(--mode tailscale) ;;
    --local) task_mode=(--mode local) ;;
    --no-ai) task_ai=(--no-ai) ;;
    *) echo 'Usage: scripts/start.sh [--local|--tailscale] [--no-ai]' >&2; exit 2 ;;
  esac
done
command -v python3 >/dev/null || { echo 'Install Python 3 first.' >&2; exit 1; }
docker compose version >/dev/null || { echo 'Install Docker Engine and the Compose plugin first.' >&2; exit 1; }
python3 scripts/configure.py "${task_mode[@]}" "${task_ai[@]}"
./scripts/compose.sh up -d --build --remove-orphans
python3 - <<'PY'
import json, sys, time, urllib.request
sys.path.insert(0, 'scripts')
from configure import read_values
values = read_values()
port = values.get('APP_PORT', '8080')
for attempt in range(90):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/setup/status', timeout=3) as response:
            status = json.load(response)
        print('Application ready: ' + values.get('APP_ORIGIN', 'http://localhost:' + port))
        if status.get('required'):
            print('First-run setup token: ' + values.get('BOOTSTRAP_TOKEN', ''))
            print('Create your administrator account in the first-run page.')
        break
    except Exception:
        if attempt == 89:
            raise SystemExit('Application did not become ready. Run ./scripts/diagnose.sh and inspect migrate/api logs.')
        time.sleep(2)
PY
if [[ " ${task_mode[*]} " == *' tailscale '* ]]; then
  ./scripts/tailscale-serve.sh
fi
