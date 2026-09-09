-- THE SEQUENCE EDITOR: step 1 timing, per-step enable, and an entry timestamp.
--
-- ============================================================================
-- 1. STEP 1 CANNOT BE N DAYS FROM ITSELF
-- ============================================================================
--
-- Every step had delay_days measured from leads.emailed_at. That is right for
-- steps 2+ and MEANINGLESS for step 1, because STEP 1 IS THE FIRST SEND - it is
-- what creates emailed_at. A "day 0" field on it read as a real control and was
-- not one.
--
-- An imported lead needs real timing there: immediately on entering the drip,
-- or after N minutes, or after N hours. So step 1 gets delay_minutes, measured
-- from when the lead ENTERED the drip.
--
-- ⚠️ THIS IS NOT THE "TWO ANCHORS" FAULT that was rejected when the import was
--    designed. That would have been a sequence_started_at DUPLICATING
--    emailed_at for the same event. These measure DIFFERENT events:
--
--      drip_entered_at   when step 1 fires
--      emailed_at        what steps 2+ measure from - and step 1 CREATES it
--
--    One value cannot serve both, because at the moment step 1 is due the other
--    does not exist yet.
ALTER TABLE leads ADD COLUMN drip_entered_at timestamptz;

COMMENT ON COLUMN leads.drip_entered_at IS
  'When this lead joined its drip. Step 1 fires at drip_entered_at + drip_steps.delay_minutes; steps 2+ fire at emailed_at + delay_days, and step 1 is what creates emailed_at. Different events, not a duplicate anchor.';

-- Backfill: a lead already on a drip entered it when its first email went, or
-- now if none has. Nothing is due differently as a result - step 1 is already
-- recorded as sent for every call-sourced lead on a drip.
UPDATE leads SET drip_entered_at = COALESCE(emailed_at, now())
 WHERE drip_campaign_id IS NOT NULL AND drip_entered_at IS NULL;

ALTER TABLE drip_steps
  ADD COLUMN delay_minutes int
    CONSTRAINT drip_steps_delay_minutes_sane
    CHECK (delay_minutes IS NULL OR (delay_minutes >= 0 AND delay_minutes <= 10080));

COMMENT ON COLUMN drip_steps.delay_minutes IS
  'STEP 1 ONLY: minutes after leads.drip_entered_at before step 1 sends. 0 = immediately. Capped at 10080 (a week) - past that, use a day-based step. NULL on steps 2+, which use delay_days from emailed_at instead. Only affects IMPORTED leads: a call-sourced lead already had email 1 sent by api/sender.py, and enter() links that send to step 1.';

-- Existing sequences: step 1 sent immediately, which is what position=1 already
-- meant before this column existed.
UPDATE drip_steps SET delay_minutes = 0 WHERE position = 1 AND delay_minutes IS NULL;

-- ============================================================================
-- 2. A STEP CAN BE TURNED OFF WITHOUT DELETING IT
-- ============================================================================
--
-- Deleting a step soft-deletes it and is permanent as far as the sequence is
-- concerned. Turning one off is a different intent: try the sequence without
-- step 3 and put it back. Without this the only way to test that is to delete
-- and retype the copy.
--
-- A DISABLED STEP IS SKIPPED, NOT SENT - and its existing send records stand, so
-- a lead that already had it does not get it again if it is re-enabled.
ALTER TABLE drip_steps
  ADD COLUMN enabled boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN drip_steps.enabled IS
  'false = skipped by drip.due() but kept, with its copy and its send records. Distinct from deleted_at, which removes it from the sequence. Delay ORDERING is still validated across disabled steps, so re-enabling one can never produce a backwards sequence.';
