#!/usr/bin/env bash
# Phase 1 puts leads in by hand.
#   ./scripts/add_lead.sh "Firm Name" +15551234567 America/Los_Angeles
#
# pool_status is set to 'active' so the dialer will pick it up. The dial guard
# still applies: if the number is not in DIAL_ALLOWLIST it is refused and
# audited, never dialed.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

COMPANY="${1:?usage: add_lead.sh <company> <phone_e164> <iana_timezone>}"
PHONE="${2:?phone required, E.164 e.g. +15551234567}"
TZ_NAME="${3:?IANA timezone required, e.g. America/Los_Angeles}"

docker compose exec -T caller-postgres psql -v ON_ERROR_STOP=1 \
  -U "$CALLER_DB_USER" -d "$CALLER_DB_NAME" <<SQL
INSERT INTO leads (company, phone_e164, timezone, pool_status, status)
VALUES ('$COMPANY', '$PHONE', '$TZ_NAME', 'active', 'new')
-- leads_phone_uniq is PARTIAL since migration 038, and a partial index
-- cannot arbitrate ON CONFLICT unless the statement repeats its WHERE.
ON CONFLICT (phone_e164) WHERE phone_e164 IS NOT NULL DO UPDATE
   SET pool_status='active', status='new', next_attempt_at=now(),
       updated_at=now()
RETURNING lead_id, company, phone_e164, pool_status, status;
SQL
