#!/usr/bin/env bash
# A THROWAWAY DRIP CAMPAIGN, so verifying a save never writes to a real one.
#
# The served-page rule says fetch the real route before reporting a UI fix. That
# rule is right and it has a hole: verifying a SAVE means POSTing, and a POST to
# /campaign/<id>/steps REPLACES that campaign's sequence. On 2026-09-10 I ran
# those POSTs against Sean's live drip while checking a fix, and his four steps
# of real copy were replaced with b1/b2/b3/b4. They were recoverable only because
# save_steps SOFT-deletes - which is luck standing in for a rule.
#
#   ./scripts/scratch_drip.sh new     -> prints a fresh campaign_id to POST at
#   ./scripts/scratch_drip.sh list    -> every scratch campaign still lying around
#   ./scripts/scratch_drip.sh clean   -> deletes all of them
#
# The name prefix is the whole mechanism: a scratch campaign says so on the
# /campaigns screen, and leftovers are one command to find. DRIP-LIVETEST and
# DRIP-REPRO sat in the dev database for days because nothing labelled them.
set -euo pipefail
PREFIX='SCRATCH-'
PSQL=(docker exec -i caller-postgres psql -U calleruser -d caller_db -v ON_ERROR_STOP=1 -t -A)

case "${1:-}" in
new)
  NAME="${PREFIX}$(date +%H%M%S)"
  # CREATED THROUGH THE APP, not with an INSERT. A hand-written row missed
  # agent_l1_version and every other NOT NULL default, and a scratch campaign
  # that is not shaped like a real one verifies nothing.
  curl -fsS -o /dev/null -X POST "${BASE:-http://localhost:4100}/campaigns/new" \
       --data-urlencode "name=${NAME}" --data-urlencode 'campaign_type=drip' \
       --data-urlencode 'notes=scratch - safe to delete, made by scratch_drip.sh'
  "${PSQL[@]}" -c "SELECT campaign_id FROM campaign_configs WHERE name = '${NAME}'"
  echo "  ^ ${NAME} - POST your verification at this one, then: $0 clean" >&2
  ;;
list)
  "${PSQL[@]}" -c "SELECT campaign_id, name FROM campaign_configs
                    WHERE name LIKE '${PREFIX}%' ORDER BY name"
  ;;
clean)
  "${PSQL[@]}" <<SQL
BEGIN;
DELETE FROM email_sends WHERE step_id IN (
  SELECT step_id FROM drip_steps WHERE campaign_id IN (
    SELECT campaign_id FROM campaign_configs WHERE name LIKE '${PREFIX}%'));
DELETE FROM drip_steps WHERE campaign_id IN (
  SELECT campaign_id FROM campaign_configs WHERE name LIKE '${PREFIX}%');
DELETE FROM campaign_windows WHERE campaign_id IN (
  SELECT campaign_id FROM campaign_configs WHERE name LIKE '${PREFIX}%');
-- ⚠️ REFUSES rather than orphaning: a scratch campaign with leads on it is not
-- scratch any more, and deleting it would strand them mid-sequence.
DO \$\$
BEGIN
  IF EXISTS (SELECT 1 FROM leads l JOIN campaign_configs c
              ON c.campaign_id IN (l.drip_campaign_id, l.campaign_id)
             WHERE c.name LIKE '${PREFIX}%') THEN
    RAISE EXCEPTION 'a SCRATCH campaign has leads on it - not deleting anything';
  END IF;
END \$\$;
DELETE FROM campaign_configs WHERE name LIKE '${PREFIX}%';
COMMIT;
SQL
  echo "scratch campaigns deleted" >&2
  ;;
*)
  echo "usage: $0 {new|list|clean}" >&2; exit 2 ;;
esac
