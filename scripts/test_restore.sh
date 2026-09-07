#!/usr/bin/env bash
# PROVE the suppression backup restores. Not assumed - executed.
#
# Downloads the newest backup from Spaces, restores it into a scratch
# database, and compares row-for-row against live. Drops the scratch db after.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
SCRATCH=caller_restore_test

echo "==> 1/5 fetch newest backup from Spaces"
docker compose exec -T caller-api python -c "
import gzip
from api import backup
from api.config import load_config
cfg = load_config()
objs = backup.list_backups(cfg)
assert objs, 'no backups in Spaces'
key = objs[0]['Key']
blob = backup.fetch(cfg, key)
open('/tmp/restore.sql','wb').write(gzip.decompress(blob))
print(f'    {key}  ({objs[0][\"Size\"]} bytes gz)')
"

echo "==> 2/5 create a scratch database"
docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" \
  -tAc "DROP DATABASE IF EXISTS $SCRATCH;" >/dev/null
docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" \
  -tAc "CREATE DATABASE $SCRATCH;" >/dev/null
echo "    $SCRATCH"

echo "==> 3/5 restore into it"
docker compose exec -T caller-api cat /tmp/restore.sql \
  | docker compose exec -T caller-postgres psql -v ON_ERROR_STOP=1 \
      -U "$CALLER_DB_USER" -d "$SCRATCH" >/dev/null
echo "    restored"

echo "==> 4/5 compare row-for-row against live"
LIVE=$(docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" \
        -tAc "SELECT md5(string_agg(phone_e164||'|'||reason||'|'||coalesce(source,''), ',' ORDER BY phone_e164)) FROM suppression" | tr -d ' \r')
REST=$(docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$SCRATCH" \
        -tAc "SELECT md5(string_agg(phone_e164||'|'||reason||'|'||coalesce(source,''), ',' ORDER BY phone_e164)) FROM suppression" | tr -d ' \r')
NLIVE=$(docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" -tAc "SELECT count(*) FROM suppression" | tr -d ' \r')
NREST=$(docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$SCRATCH" -tAc "SELECT count(*) FROM suppression" | tr -d ' \r')
echo "    live    : $NLIVE rows  md5=$LIVE"
echo "    restored: $NREST rows  md5=$REST"

echo "==> 5/5 cleanup"
docker compose exec -T caller-postgres psql -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" \
  -tAc "DROP DATABASE $SCRATCH;" >/dev/null
docker compose exec -T caller-api rm -f /tmp/restore.sql || true

if [[ "$LIVE" == "$REST" && "$NLIVE" == "$NREST" ]]; then
  echo "RESTORE VERIFIED: content identical."
else
  echo "RESTORE FAILED: content differs."; exit 1
fi
