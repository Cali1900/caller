-- ⚠️ WIRING AND GATE ARE TWO DIFFERENT QUESTIONS. BOTH MUST PASS.
--
--   WIRING   C1 -> Drip 3         WHERE this campaign's leads go
--   GATE     accepts: emailed     WHETHER a lead is ready to receive
--
-- Migration 044 dropped default_drip_id on the grounds that status replaced it.
-- IT DID NOT, and this restores it. Status cannot express DESTINATION:
--
--   A California drip and a Hawaii drip both accept `emailed`. Under gate-only,
--   every California lead receives the Hawaii sequence too.
--
-- Status cannot carry geography, or which campaign sourced a lead, and it must not
-- be made to - that is the one-field-two-jobs fault this codebase keeps paying
-- for. "Dumb" meant the system does not get clever about CHOOSING; it did not mean
-- route to everything that matches.
--
-- ⚠️ IMPORTED LEADS ARE THE DELIBERATE ASYMMETRY. Wiring is a property of the CALL
-- campaign, and an imported lead has none - so for them the gate is the only
-- condition. Expressed as `campaign_id IS NULL` rather than `lead_source =
-- 'import'`, because lead_source is the RECORD of where a lead came from and the
-- absence of a call campaign is the structural fact that actually matters: a lead
-- given a phone and moved to a campaign is wired by it from then on, whatever its
-- provenance says.
ALTER TABLE campaign_configs
  ADD COLUMN default_drip_id uuid REFERENCES campaign_configs(campaign_id);

COMMENT ON COLUMN campaign_configs.default_drip_id IS
  'Call campaigns only: WHERE this campaign''s leads go after email 1. A lead '
  'receives a step only if it is wired here AND its status is in that drip''s '
  'accepted_statuses. NULL is legitimate and means no follow-up - the screen '
  'says so, because a silent null is how C1 ended up wired to nothing.';

-- Sean asked for C1 -> Drip 1 for now; he repoints it himself later.
UPDATE campaign_configs SET default_drip_id =
    (SELECT campaign_id FROM campaign_configs WHERE name = 'Drip 1')
 WHERE name = 'C1';
