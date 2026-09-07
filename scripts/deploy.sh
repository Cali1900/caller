#!/usr/bin/env bash
# THE ONLY WAY TO RESTART CALLER.
#
# Nothing restarts a container during calling hours without pausing first.
# Baked in here rather than relying on anyone remembering: a restart mid-call
# drops that call, and a restart mid-batch can leave leads stuck in 'dialing'.
#
#   ./scripts/deploy.sh              # rebuild + restart everything, safely
#   ./scripts/deploy.sh caller-api   # just one service
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICES="${*:-caller-api caller-worker}"

state() {
  docker compose exec -T caller-api python -c "
from api import settings
print('on' if settings.all_settings(force=True)['dialing_enabled'] else 'off')" 2>/dev/null || echo unknown
}
set_dialing() {
  docker compose exec -T caller-api python -c "
from api import settings
settings.set_many({'dialing_enabled': '$1'}, updated_by='deploy.sh')
print('  dialing ->', settings.all_settings(force=True)['dialing_enabled'])"
}

BEFORE=$(state)
echo "==> dialing is currently: $BEFORE"

RESUME=0
if [[ "$BEFORE" == "on" ]]; then
  echo "==> pausing before restart (a restart mid-call drops that call)"
  set_dialing false
  RESUME=1
  # let an in-flight tick finish rather than yanking the process mid-dial
  sleep 3
else
  echo "==> already paused; nothing to pause"
fi

restore() {
  if [[ $RESUME -eq 1 ]]; then
    echo "==> resuming dialing"
    set_dialing true || echo "!! COULD NOT RESUME - turn it on at /campaign"
  fi
}
trap restore EXIT      # resume even if the build fails

echo "==> rebuilding: $SERVICES"
docker compose up -d --build --no-deps $SERVICES

echo "==> waiting for health"
for i in $(seq 1 30); do
  bad=$(docker compose ps --format '{{.Name}} {{.Status}}' | grep -c 'unhealthy' || true)
  starting=$(docker compose ps --format '{{.Name}} {{.Status}}' | grep -c 'health: starting' || true)
  [[ "$bad" == "0" && "$starting" == "0" ]] && break
  sleep 2
done
docker compose ps --format '{{.Name}}\t{{.Status}}' | sed 's/^/    /'
