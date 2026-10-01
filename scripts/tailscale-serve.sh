#!/usr/bin/env bash
# Private tailnet HTTPS only. This never enables Funnel.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
python3 - <<'PY'
import json, subprocess, sys
sys.path.insert(0, 'scripts')
from configure import read_values, tailscale_origin
values = read_values()
origin = tailscale_origin()
if values.get('APP_ORIGIN') != origin or values.get('SESSION_COOKIE_SECURE') != 'true':
    raise SystemExit('Configure this installation using ./scripts/start.sh --tailscale first.')
port = values.get('APP_PORT', '8080')
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit('APP_PORT is invalid.')
target = 'http://127.0.0.1:' + port
status = subprocess.run(['tailscale', 'serve', 'status', '--json'], capture_output=True, text=True)
if status.returncode:
    raise SystemExit('Cannot inspect Tailscale Serve. Run with the Tailscale operator account or grant host permission.')
config = json.loads(status.stdout or '{}')
proxies = []
def visit(item):
    if isinstance(item, dict):
        if 'Proxy' in item:
            proxies.append(item['Proxy'])
        for value in item.values(): visit(value)
    elif isinstance(item, list):
        for value in item: visit(value)
visit(config)
if (config and not proxies) or any(proxy not in (target, 'http://localhost:' + port) for proxy in proxies):
    raise SystemExit('An existing Tailscale Serve proxy uses another target. Review it before assigning HTTPS port 443 to this app.')
subprocess.run(['tailscale', 'serve', '--bg', '--https=443', target], check=True)
print('Private HTTPS: ' + origin)
print('Testers must be connected to the approved tailnet. App login and workspace roles still apply.')
PY
