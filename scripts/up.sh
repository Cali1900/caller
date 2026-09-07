#!/usr/bin/env bash
# Bring the stack up in the required order:
#   postgres -> migrations -> api + worker
#
# The ordering is the point. Migrations run BEFORE the app containers come
# up, so a container never boots against a schema it does not match.
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/migrate.sh
echo "==> starting caller-api and caller-worker"
docker compose up -d
docker compose ps
