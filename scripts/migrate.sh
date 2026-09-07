#!/usr/bin/env bash
# Forward-only migrations. Never edit an applied migration - add a new one.
#
# Applied BEFORE caller-api and caller-worker come up. That ordering is a
# standing rule across this estate, so scripts/up.sh enforces it rather than
# relying on anyone remembering.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "FATAL: .env missing. Copy .env.example and fill it in." >&2
  exit 1
fi
set -a; . ./.env; set +a

: "${CALLER_DB_NAME:?CALLER_DB_NAME not set}"
: "${CALLER_DB_USER:?CALLER_DB_USER not set}"

PSQL=(docker exec -i caller-postgres psql -v ON_ERROR_STOP=1 -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME")

echo "==> ensuring caller-postgres is up and healthy"
docker compose up -d caller-postgres >/dev/null
for i in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' caller-postgres 2>/dev/null || echo starting)
  [[ "$state" == "healthy" ]] && break
  [[ $i -eq 60 ]] && { echo "FATAL: caller-postgres never became healthy" >&2; exit 1; }
  sleep 1
done
echo "    healthy"

"${PSQL[@]}" -q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

applied_any=0
for f in $(ls -1 migrations/*.sql | sort); do
  name=$(basename "$f")
  seen=$("${PSQL[@]}" -tAc "SELECT 1 FROM schema_migrations WHERE filename='$name'")
  if [[ "$seen" == "1" ]]; then
    echo "==> skip   $name (already applied)"
    continue
  fi
  echo "==> apply  $name"
  # -1 wraps the file in a single transaction: a failure leaves NOTHING
  # half-applied.
  docker exec -i caller-postgres psql -v ON_ERROR_STOP=1 -1 \
      -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" < "$f"
  "${PSQL[@]}" -q -c "INSERT INTO schema_migrations (filename) VALUES ('$name')"
  echo "    applied"
  applied_any=1
done

[[ $applied_any -eq 0 ]] && echo "==> nothing to apply"
echo "==> migrations complete"
