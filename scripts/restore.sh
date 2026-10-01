#!/usr/bin/env bash
# Restore only to an empty database and empty file volume; never drops existing data.
set -euo pipefail
umask 077
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if (( $# != 1 )) || [[ ! -f "$1" || ! -r "$1" ]]; then
  echo 'Usage: restore.sh readable-backup.tar.gz' >&2; exit 2
fi
task_archive="$1"
task_running="$("$task_root/scripts/compose.sh" ps --status running --services)"
if [[ $'\n'"$task_running"$'\n' =~ $'\n'(api|scheduler|executor|connectors|meetings|migrate)$'\n' ]]; then
  echo 'Stop api, scheduler, executor, connectors, meetings, and migrate before restore.' >&2; exit 1
fi
task_tmp="$(mktemp -d)"
trap 'rm -rf -- "$task_tmp"' EXIT
python3 - "$task_archive" "$task_tmp" <<'PY'
import hashlib, json
from pathlib import Path
import sys, tarfile
def sha256_stream(content):
    digest = hashlib.sha256()
    for chunk in iter(lambda: content.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()
archive, directory = sys.argv[1], Path(sys.argv[2])
expected = {'database.dump', 'files.tar.gz', 'manifest.json'}
with tarfile.open(archive, 'r:gz') as source:
    members = source.getmembers()
    if len(members) != 3 or {m.name for m in members} != expected or any(not m.isfile() for m in members):
        raise SystemExit('Invalid backup bundle: expected exactly three regular files.')
    for member in members:
        with source.extractfile(member) as content, (directory / member.name).open('xb') as output:
            import shutil
            shutil.copyfileobj(content, output)
manifest = json.loads((directory / 'manifest.json').read_text())
if manifest.get('format') != 1:
    raise SystemExit('Unsupported backup format.')
for name in ('database.dump', 'files.tar.gz'):
    with (directory / name).open('rb') as content:
        actual = sha256_stream(content)
    if manifest.get('files', {}).get(name) != actual:
        raise SystemExit('Backup checksum failed: ' + name)
PY
python3 "$task_root/scripts/volume_archive.py" validate --archive "$task_tmp/files.tar.gz"
"$task_root/scripts/compose.sh" exec -T db pg_restore --list < "$task_tmp/database.dump" > /dev/null
task_objects="$("$task_root/scripts/compose.sh" exec -T db sh -c \
  'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('"'"'pg_catalog'"'"','"'"'information_schema'"'"') AND n.nspname NOT LIKE '"'"'pg_toast%'"'"' AND n.nspname NOT LIKE '"'"'pg_temp_%'"'"';"')"
if [[ "$task_objects" != "0" ]]; then
  echo 'Refusing restore: database contains objects. Use a fresh Compose project and volume.' >&2; exit 1
fi
"$task_root/scripts/compose.sh" run --rm --no-deps -T api python /app/scripts/volume_archive.py empty
"$task_root/scripts/compose.sh" exec -T db sh -c \
  'exec pg_restore --single-transaction --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$task_tmp/database.dump"
"$task_root/scripts/compose.sh" run --rm --no-deps -T api \
  python /app/scripts/volume_archive.py unpack < "$task_tmp/files.tar.gz"
echo 'Restored database and original files. Restore the matching encryption key, apply migrations, then start application services.'
echo 'If file restoration failed after database restore, retry in another fresh project; existing data is never overwritten.'
