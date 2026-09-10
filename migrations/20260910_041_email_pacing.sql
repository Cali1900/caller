-- SENDING PACE: a gap, an hourly cap, a daily cap, and business hours.
--
-- ============================================================================
-- WHY: THE SENDER HAD NO PACING AT ALL
-- ============================================================================
--
-- The dialer has spacing (dial_interval_min/max, jittered, re-rolled every
-- tick) and a per-campaign daily cap. The sender had NEITHER. drip.run_once
-- drained up to 50 due leads in a tight loop and the worker re-entered it every
-- 120 seconds, so 100 leads entering a drip meant 50 emails in a few seconds,
-- then 50 more two minutes later - a sustained ceiling of ~1,500/hour from one
-- mailbox. `limit=50` was a query-cost decision, never a rate.
--
-- A burst like that is the one mistake here that cannot be undone. A dropped
-- step is resent in a minute; a domain reputation is earned back over months.
--
-- ============================================================================
-- THE FOUR LAYERS, AND WHY THE HOURLY CAP IS THE REAL THROTTLE
-- ============================================================================
--
--   gap 60-300s jittered   stops two sends sharing a second
--   hourly cap             THE PACE. A gap alone permits 60/hour at its widest
--                          and 12/hour is the target, so the cap is what binds
--   daily cap              the volume ceiling, and the warm-up dial
--   business hours         nobody legitimate sends a law firm mail at 3am
--
-- ⚠️ LIMITS ARE PER CAMPAIGN, COUNTS ARE PER MAILBOX. Reputation belongs to the
--    ADDRESS, not to whichever campaign happens to be sending. Two drips on
--    info@counselorai.io with 15/hour each would put 30/hour on one mailbox, so
--    the count spans every campaign sharing that sender_email. The tighter
--    campaign is bound by the shared total, which is the conservative reading.
--
-- ⚠️ THE CEILING IS IN THE DATABASE, not only in the form. daily_cap is capped
--    at 250 by CHECK: the call-side daily_cap allows 5000, and a 5000-email day
--    from a new domain is not a warm-up, it is a blacklisting. The lesson from
--    step 1's timing applies - a control must not offer a value the save
--    refuses, and the constraint is the authority both agree on.
ALTER TABLE campaign_configs
  ADD COLUMN email_gap_min_seconds int  NOT NULL DEFAULT 60,
  ADD COLUMN email_gap_max_seconds int  NOT NULL DEFAULT 300,
  ADD COLUMN email_hourly_cap      int  NOT NULL DEFAULT 15,
  ADD COLUMN email_daily_cap       int  NOT NULL DEFAULT 50,
  ADD CONSTRAINT email_gap_sane
      CHECK (email_gap_min_seconds >= 15 AND email_gap_max_seconds <= 7200
             AND email_gap_min_seconds <= email_gap_max_seconds),
  ADD CONSTRAINT email_hourly_cap_sane
      CHECK (email_hourly_cap >= 1 AND email_hourly_cap <= 60),
  ADD CONSTRAINT email_daily_cap_sane
      CHECK (email_daily_cap >= 1 AND email_daily_cap <= 250);

-- Counting sends per mailbox per hour and per day is now on the hot path of
-- every selection. sent_at is the column both caps read, and NULL sent_at means
-- prepared-but-not-sent, which must not count against a cap.
CREATE INDEX IF NOT EXISTS email_sends_sent_at
    ON email_sends (sent_at) WHERE sent_at IS NOT NULL;

COMMENT ON COLUMN campaign_configs.email_hourly_cap IS
  'Emails per rolling hour. Counted PER MAILBOX (sender_email) across every '
  'campaign sharing it, because reputation belongs to the address.';
COMMENT ON COLUMN campaign_configs.email_daily_cap IS
  'Emails per operator-timezone day, counted per mailbox. Start at 50; the '
  'CHECK refuses above 250.';
