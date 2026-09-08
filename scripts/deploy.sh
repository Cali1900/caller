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

# THE PAUSE IS campaign_configs.is_running.
#
# This used to read settings['dialing_enabled'], which nothing has consulted
# since campaigns became named configurations - so the pause was a NO-OP and a
# deploy during calling hours would have rebuilt straight through a live call
# while reporting that it had paused. Same failure as the masked guards: it
# wrote one place and the dialer read another.
running_campaign() {
  docker compose exec -T caller-api python -c "
from api import campaigns
r = campaigns.running()
print(r['campaign_id'] if r else '')" 2>/dev/null | tr -d '\r' | tail -1
}
stop_campaign() {
  docker compose exec -T caller-api python -c "
from api import campaigns
campaigns.stop()
print('  running now:', campaigns.running() or 'nothing')"
}
start_campaign() {
  docker compose exec -T caller-api python -c "
from api import campaigns
campaigns.start('$1')
r = campaigns.running()
print('  running now:', r['name'] if r else 'NOTHING - start it at /campaigns')"
}

RUNNING=$(running_campaign)
if [[ -n "$RUNNING" ]]; then
  echo "==> a campaign is RUNNING ($RUNNING)"
  echo "==> stopping it before restart (a restart mid-call drops that call)"
  stop_campaign
  # let an in-flight tick finish rather than yanking the process mid-dial
  sleep 3
else
  echo "==> no campaign running; nothing to pause"
fi

restore() {
  if [[ -n "$RUNNING" ]]; then
    echo "==> restarting the campaign that was running"
    start_campaign "$RUNNING" || echo "!! COULD NOT RESUME - start it at /campaigns"
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
