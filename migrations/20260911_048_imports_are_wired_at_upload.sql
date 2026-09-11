-- ⚠️ AN IMPORTED BATCH IS WIRED WHEN IT IS UPLOADED.
--
-- Migration 047 restored the wiring for call-sourced leads and left imported ones
-- entering on the GATE ALONE, because an imported lead has no call campaign to be
-- wired by. That carve-out has the same flaw 047 corrected, one layer down:
--
--   Import a California list and a Hawaii list. Both are `imported`. Every drip
--   accepting `imported` receives BOTH.
--
-- The fix is the same fix: an explicit decision made by a person, once, about
-- where a batch goes. For a call-sourced lead that decision lives on the campaign;
-- an imported lead has no campaign, so THE BATCH IS THE UNIT and the choice is
-- made at upload. Same shape, different owner.
--
-- ⚠️ THIS IS NOT THE ASSIGNMENT COLUMN COMING BACK. drip_campaign_id was
-- "which drip owns this lead", a second fact that could disagree with status. This
-- is WIRING ONLY - the gate still applies, every selection, and a wired lead whose
-- status leaves the accepted set stops exactly as a call-sourced one does. Both
-- conditions, uniformly, for every lead.
ALTER TABLE leads
  ADD COLUMN import_drip_id uuid REFERENCES campaign_configs(campaign_id);

COMMENT ON COLUMN leads.import_drip_id IS
  'For a lead with no call campaign: WHICH drip its batch was wired to at upload. '
  'The gate still decides WHETHER it receives anything. NULL means the batch named '
  'no drip, so the lead is wired nowhere and receives nothing - surfaced on /today '
  'rather than left silent.';

CREATE INDEX IF NOT EXISTS leads_import_drip ON leads (import_drip_id)
    WHERE import_drip_id IS NOT NULL;
