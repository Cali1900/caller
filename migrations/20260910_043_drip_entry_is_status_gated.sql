-- DRIP MEMBERSHIP IS DERIVED FROM STATUS, not stored on the lead.
--
-- ============================================================================
-- WHY: TWO FACTS SAYING ONE THING
-- ============================================================================
--
-- `leads.status` said where a lead was; `leads.drip_campaign_id` said which drip
-- owned it. They could disagree, and a disagreement meant a sequence still
-- queued for a firm that had moved on. Guarding that is a gate per disagreement;
-- DERIVING membership from status makes the disagreement impossible.
--
-- A drip declares which statuses it accepts. A lead's status decides whether it
-- qualifies. That is the whole mechanism - re-evaluated on every selection, so a
-- status change stops or starts sending immediately, mid-sequence, without
-- anything having to notice and act.
--
-- OVERLAP IS ALLOWED: two running drips accepting one status both send to a lead
-- with it. That is legitimate - product news and a follow-up sequence are
-- different conversations - so the UI WARNS at config time rather than refusing.
ALTER TABLE campaign_configs
  ADD COLUMN accepted_statuses text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN campaign_configs.accepted_statuses IS
  'Drip campaigns only: the lead statuses this drip accepts. Membership is '
  'DERIVED from this on every selection - there is no assignment column. Empty '
  'means the drip accepts nobody and sends nothing.';

-- ============================================================================
-- STEP 1 NEEDS AN ANCHOR THAT SURVIVES THE ASSIGNMENT COLUMN
-- ============================================================================
--
-- ⚠️ Step 1's delay is measured "from joining the drip", and joining was an EVENT
-- (`drip_entered_at`, written by enter()). With membership derived there is no
-- event to stamp - a lead simply qualifies or does not.
--
-- The honest replacement is WHEN THE STATUS LAST CHANGED, because that is the
-- moment the lead began to qualify. A TRIGGER maintains it rather than every
-- writer remembering: status is written from the dialer, the scorer, the archive
-- sweep, /dnc, the lead page and the bulk actions, and a column that depends on
-- all of them remembering is a column that is wrong. Same argument as the IANA
-- timezone trigger already on this table.
ALTER TABLE leads ADD COLUMN status_changed_at timestamptz;

UPDATE leads SET status_changed_at = coalesce(drip_entered_at, updated_at,
                                              created_at);
ALTER TABLE leads ALTER COLUMN status_changed_at SET NOT NULL;

CREATE OR REPLACE FUNCTION leads_stamp_status_change() RETURNS trigger AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    NEW.status_changed_at := now();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS leads_status_changed_at ON leads;
CREATE TRIGGER leads_status_changed_at
  BEFORE UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION leads_stamp_status_change();

COMMENT ON COLUMN leads.status_changed_at IS
  'When status last changed, maintained by trigger. Step 1 of a drip is timed '
  'from here: with membership derived from status, this is the moment the lead '
  'began to qualify - the replacement for drip_entered_at.';
