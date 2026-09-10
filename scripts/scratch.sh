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
# ⚠️ RENAMED from scratch_drip.sh on 2026-09-10. The name said "drip" while it
# already made call campaigns and now makes LEADS, and a name that describes less
# than the thing does is how the rule got read as "drips only" - which is exactly
# why writing to live data recurred after the rule was written.
#
#   ./scripts/scratch.sh new        -> a scratch DRIP campaign, prints its id
#   ./scripts/scratch.sh new-call   -> a scratch CALL campaign
#   ./scripts/scratch.sh new-lead   -> a scratch LEAD, prints its lead_id
#   ./scripts/scratch.sh snapshot   -> record every live table before verifying
#   ./scripts/scratch.sh diff       -> what changed since the snapshot
#   ./scripts/scratch.sh list / clean
#
# The name prefix is the whole mechanism: a scratch campaign says so on the
# /campaigns screen, and leftovers are one command to find. DRIP-LIVETEST and
# DRIP-REPRO sat in the dev database for days because nothing labelled them.
set -euo pipefail
PREFIX='SCRATCH-'
SNAP=${SNAP:-.scratch_snapshot}
PSQL=(docker exec -i caller-postgres psql -U calleruser -d caller_db -v ON_ERROR_STOP=1 -t -A)

case "${1:-}" in
new|new-call)
  # ⚠️ CALL CAMPAIGNS TOO, ADDED AFTER MAKING THE MISTAKE A SECOND TIME. The rule
  # said verification may read live data and never write it, and then a full
  # config POST went to the live C1 to check a new selector - changing its live
  # prompt version and blanking its notes. A rule with no safe target for half
  # the cases is a rule that gets broken on the other half.
  TYPE='drip'; [ "$1" = 'new-call' ] && TYPE='call'
  NAME="${PREFIX}$(date +%H%M%S)"
  # CREATED THROUGH THE APP, not with an INSERT. A hand-written row missed
  # agent_l1_version and every other NOT NULL default, and a scratch campaign
  # that is not shaped like a real one verifies nothing.
  curl -fsS -o /dev/null -X POST "${BASE:-http://localhost:4100}/campaigns/new" \
       --data-urlencode "name=${NAME}" --data-urlencode "campaign_type=${TYPE}" \
       --data-urlencode 'notes=scratch - safe to delete, made by scratch_drip.sh'
  "${PSQL[@]}" -c "SELECT campaign_id FROM campaign_configs WHERE name = '${NAME}'"
  echo "  ^ ${NAME} - POST your verification at this one, then: $0 clean" >&2
  ;;
new-lead)
  # A SCRATCH LEAD, so verifying anything that writes to `leads` has a target
  # that is not one of Sean's 1,087. Company carries the prefix so `list` and
  # `clean` find it, and it is deliberately NOT dialable: no phone, pool status
  # 'pool', so it cannot be claimed by a running campaign even by accident.
  NAME="${PREFIX}$(date +%H%M%S)"
  "${PSQL[@]}" >/dev/null <<SQL_END
INSERT INTO leads (company, dm_email, state, lead_source, pool_status, status)
     VALUES ('${NAME}', 'scratch+$(date +%s)@example.invalid', 'CA',
             'import', 'pool', 'imported');
SQL_END
  # The id on its own line: RETURNING prints the value AND psql prints its
  # command tag, and a caller doing CID=$(...) got both.
  "${PSQL[@]}" -c "SELECT lead_id FROM leads WHERE company = '${NAME}'"
  echo "  ^ ${NAME} - a scratch LEAD. Not dialable, no phone." >&2
  ;;

snapshot)
  # ⚠️ THE BEFORE PICTURE. Row count and a content hash per live table, so an
  # accidental write to real data is VISIBLE within seconds instead of being
  # found days later from its consequences.
  #
  # This is the part that closes the hole. A scratch target only helps when you
  # remember to use one; the diff catches the time you did not.
  "${PSQL[@]}" -c "
    SELECT string_agg(t || '=' || n || ':' || h, E'\n' ORDER BY t) FROM (
      SELECT 'leads' AS t, count(*) AS n,
             md5(string_agg(lead_id::text || status || coalesce(dm_email,'') ||
                            coalesce(phone_e164,''), '' ORDER BY lead_id)) AS h
        FROM leads WHERE company NOT LIKE '${PREFIX}%'
      UNION ALL SELECT 'campaign_configs', count(*),
             md5(string_agg(campaign_id::text || name || accepted_statuses::text,
                            '' ORDER BY campaign_id))
        FROM campaign_configs WHERE name NOT LIKE '${PREFIX}%'
      UNION ALL SELECT 'suppression', count(*),
             coalesce(md5(string_agg(phone_e164, '' ORDER BY phone_e164)), '-')
        FROM suppression
      UNION ALL SELECT 'email_do_not_send', count(*),
             coalesce(md5(string_agg(email, '' ORDER BY email)), '-')
        FROM email_do_not_send
      UNION ALL SELECT 'email_sends', count(*),
             coalesce(md5(string_agg(send_id::text, '' ORDER BY send_id)), '-')
        FROM email_sends
      UNION ALL SELECT 'email_clicks', count(*),
             coalesce(md5(string_agg(click_id::text, '' ORDER BY click_id)), '-')
        FROM email_clicks
      UNION ALL SELECT 'drip_steps', count(*),
             coalesce(md5(string_agg(step_id::text || subject || body,
                                     '' ORDER BY step_id)), '-')
        FROM drip_steps
    ) x" > "$SNAP"
  echo "snapshot written to $SNAP - run '$0 diff' after verifying" >&2
  ;;

diff)
  [[ -f "$SNAP" ]] || { echo "no snapshot - run '$0 snapshot' first" >&2; exit 2; }
  # ⚠️ THE BASELINE DOES NOT MOVE. An earlier version re-snapshotted into $SNAP
  # before comparing, so the second `diff` measured against what the FIRST one
  # recorded - and the original picture, the only one worth comparing to, was
  # gone. A baseline that follows the thing it measures is not a baseline.
  _keep=$(cat "$SNAP")
  SNAP="$SNAP.now" "$0" snapshot >/dev/null 2>&1
  printf '%s\n' "$_keep" > "$SNAP.base"
  if diff -u "$SNAP.base" "$SNAP.now" > /dev/null; then
    echo "✓ no live data changed since the snapshot"
  else
    echo "⚠️  LIVE DATA CHANGED since the snapshot:" >&2
    diff -u "$SNAP.base" "$SNAP.now" | grep -E '^[-+][a-z_]+=' >&2
    echo >&2
    echo "If that was verification rather than intent, it needs undoing - and" >&2
    echo "suppression and email_do_not_send CANNOT be undone by deleting a row:" >&2
    echo "they exist to outlive leads, so a wrong entry is a firm never called" >&2
    echo "again with nothing on screen saying why." >&2
    exit 1
  fi
  ;;

list)
  "${PSQL[@]}" -c "SELECT campaign_id, name FROM campaign_configs
                    WHERE name LIKE '${PREFIX}%' ORDER BY name"
  "${PSQL[@]}" -c "SELECT lead_id, company FROM leads
                    WHERE company LIKE '${PREFIX}%' ORDER BY company"
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
-- ⚠️ REFUSES rather than orphaning a REAL lead. This used to read
-- l.drip_campaign_id, a column the status gate dropped, so every clean errored.
-- Drip membership is derived now, so the only assignment a campaign can hold is
-- the CALL one - and a scratch campaign holding a real lead is not scratch.
DO \$\$
BEGIN
  IF EXISTS (SELECT 1 FROM leads l JOIN campaign_configs c
                  ON c.campaign_id = l.campaign_id
             WHERE c.name LIKE '${PREFIX}%'
               AND l.company NOT LIKE '${PREFIX}%') THEN
    RAISE EXCEPTION 'a SCRATCH campaign has a REAL lead on it - deleting nothing';
  END IF;
END \$\$;
DELETE FROM campaign_configs WHERE name LIKE '${PREFIX}%';
-- SCRATCH LEADS, and everything hanging off them. Ordered by dependency; the
-- FKs would refuse otherwise, and a half-deleted scratch lead is worse than
-- one left alone because it looks real.
DELETE FROM email_clicks WHERE lead_id IN
  (SELECT lead_id FROM leads WHERE company LIKE '${PREFIX}%');
DELETE FROM email_sends WHERE lead_id IN
  (SELECT lead_id FROM leads WHERE company LIKE '${PREFIX}%');
DELETE FROM email_audit WHERE lead_id IN
  (SELECT lead_id FROM leads WHERE company LIKE '${PREFIX}%');
DELETE FROM activity WHERE lead_id IN
  (SELECT lead_id FROM leads WHERE company LIKE '${PREFIX}%');
DELETE FROM email_drafts WHERE lead_id IN
  (SELECT lead_id FROM leads WHERE company LIKE '${PREFIX}%');
DELETE FROM leads WHERE company LIKE '${PREFIX}%';
COMMIT;
SQL
  echo "scratch campaigns deleted" >&2
  ;;
*)
  echo "usage: $0 {new|new-call|new-lead|snapshot|diff|list|clean}" >&2
     exit 2 ;;
esac
