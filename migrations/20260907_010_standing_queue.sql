-- The standing queue replaces the per-day campaign ritual.
--
-- Before: a campaign row per day, an enrol step, and a START button. The
-- ABSENCE of a start was what stopped "add 500 leads" becoming "dial 500 now".
-- That is a guard by omission, and it disappears the moment the ritual does.
--
-- After: ONE ongoing queue and ONE explicit switch, dialing_enabled, which
-- DEFAULTS TO FALSE. Adding leads can never start dialing. The operator turns
-- it on deliberately and it stays on until turned off.

-- first_dialed_at is how a NEW lead is counted against the daily cap exactly
-- once. Carry-overs (callbacks, L3 follow-ups, retries) are not new and go
-- ahead of new leads without consuming the cap.
ALTER TABLE leads ADD COLUMN first_dialed_at timestamptz;

COMMENT ON COLUMN leads.first_dialed_at IS
  'When this lead was FIRST ever dialed. The daily cap counts new leads by this, so a retry never consumes new-lead budget.';

CREATE INDEX leads_first_dialed ON leads (first_dialed_at)
    WHERE first_dialed_at IS NOT NULL;

-- Backfill from existing calls so historical leads are not counted as new.
UPDATE leads l SET first_dialed_at = sub.first_at
  FROM (SELECT lead_id, min(created_at) AS first_at FROM calls GROUP BY lead_id) sub
 WHERE sub.lead_id = l.lead_id AND l.first_dialed_at IS NULL;

-- The queue is ongoing, so "in the queue" is a property of the lead.
COMMENT ON COLUMN leads.pool_status IS
  'pool = uploaded and idle | active = IN THE STANDING QUEUE | done = finished. Moving to active is "add to campaign"; it does NOT start dialing.';

INSERT INTO settings (key, value, updated_by) VALUES
    -- DEFAULT PAUSED. This is the single switch that replaces the start
    -- button, and it must be off until a person turns it on.
    ('dialing_enabled', 'false', 'seed'),
    ('daily_cap',       '100',   'seed')
ON CONFLICT (key) DO NOTHING;
