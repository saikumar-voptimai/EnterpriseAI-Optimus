#!/usr/bin/env bash
# Consistent database + original-file bundle. Pauses application writers briefly.
set -euo pipefail
umask 077
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if (( $# > 1 )); then
  echo 'Usage: backup.sh [new-output.tar.gz]' >&2; exit 2
fi
task_output="${1:-$task_root/backups/voptimai-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"
if [[ -e "$task_output" || -L "$task_output" || -e "$task_output.sha256" || -L "$task_output.sha256" ]]; then
  echo 'Refusing to overwrite an existing archive or checksum.' >&2; exit 1
fi
mkdir -p -- "$(dirname -- "$task_output")"
task_tmp="$(mktemp -d "$(dirname -- "$task_output")/.voptimai-backup.XXXXXXXX")"
task_restart=()
while IFS= read -r task_service; do
  case "$task_service" in api|scheduler|executor|connectors|meetings) task_restart+=("$task_service");; esac
done < <("$task_root/scripts/compose.sh" ps --status running --services)
cleanup() {
  task_status=$?
  if (( ${#task_restart[@]} )); then "$task_root/scripts/compose.sh" start "${task_restart[@]}" || true; fi
  rm -rf -- "$task_tmp"
  exit "$task_status"
}
trap cleanup EXIT
if (( ${#task_restart[@]} )); then "$task_root/scripts/compose.sh" stop "${task_restart[@]}"; fi
"$task_root/scripts/compose.sh" exec -T db sh -c \
  'exec pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$task_tmp/database.dump"
"$task_root/scripts/compose.sh" run --rm --no-deps -T api \
  python /app/scripts/volume_archive.py pack > "$task_tmp/files.tar.gz"
test -s "$task_tmp/database.dump"
test -s "$task_tmp/files.tar.gz"
python3 - "$task_tmp" "$task_root" <<'PY'
from datetime import datetime, timezone
import hashlib, json
from pathlib import Path
import subprocess, sys
def sha256_stream(content):
    digest = hashlib.sha256()
    for chunk in iter(lambda: content.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()
root, repo = map(Path, sys.argv[1:])
revision = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True, capture_output=True)
manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'revision': revision.stdout.strip() or 'unversioned-package', 'files': {}}
for name in ('database.dump', 'files.tar.gz'):
    with (root / name).open('rb') as content:
        manifest['files'][name] = sha256_stream(content)
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
tar -czf "$task_tmp/bundle.tar.gz" -C "$task_tmp" database.dump files.tar.gz manifest.json
# Atomic no-overwrite publication, even if another backup chose the same name.
ln -- "$task_tmp/bundle.tar.gz" "$task_output"
(set -o noclobber; sha256sum -- "$task_output" > "$task_output.sha256")
echo "Backup saved: $task_output"
echo 'Store a protected .env copy separately: CREDENTIAL_ENCRYPTION_KEY is required to recover connection credentials.'
