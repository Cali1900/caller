-- EMAIL 1: MANUAL OR AUTO, per campaign.
--
-- Same pattern as the dial switch: DEFAULTS TO THE SAFE SIDE and is flipped
-- deliberately. Adding leads never starts dialing; turning on a campaign never
-- starts sending.

ALTER TABLE campaign_configs
  ADD COLUMN email_1_mode text NOT NULL DEFAULT 'manual'
      CHECK (email_1_mode IN ('manual', 'auto')),
  ADD COLUMN email_1_delay_minutes int NOT NULL DEFAULT 15
      CHECK (email_1_delay_minutes >= 0 AND email_1_delay_minutes <= 1440);

COMMENT ON COLUMN campaign_configs.email_1_mode IS
  'manual = draft waits for a person. auto = send N minutes after the call, '
  'but ONLY for leads that pass every exclusion in api/autosend.py.';

-- The firm's website. Needed by the domain check - an email domain that is
-- neither the firm's site nor known free-mail is held for review, and without
-- the website that check cannot run at all.
ALTER TABLE leads ADD COLUMN website text;

-- Why a lead was held instead of auto-sent, so it is visible on the lead
-- rather than only in a log. NULL means "not evaluated yet", which is not the
-- same as "passed" - see autosend.eligibility().
ALTER TABLE leads ADD COLUMN autosend_hold_reason text;
ALTER TABLE leads ADD COLUMN autosend_checked_at timestamptz;

COMMENT ON COLUMN leads.autosend_hold_reason IS
  'Populated when auto-send declined. NULL = never evaluated, not = eligible.';
