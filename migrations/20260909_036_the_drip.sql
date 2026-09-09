-- THE DRIP: a lead's email sequence, owned by a DRIP campaign.
--
-- ============================================================================
-- 1. THE CAMPAIGN A LEAD IS DRIPPING ON IS A SEPARATE COLUMN
-- ============================================================================
--
-- DRIP_ARCHIVE_BRIEF.md specified "the campaign_id MOVES from the call campaign
-- to the drip campaign". That is wrong, and the reason is worth keeping: it did
-- not account for leads.campaign_id doing a SECOND job.
--
-- Neither dial_audit nor calls records a campaign, so leads.campaign_id is the
-- ONLY record of which campaign dialed a lead. Moving it breaks three things:
--
--   * THE DAILY CAP LEAKS. guards.assert_under_daily_cap counts
--     `leads WHERE campaign_id = <call campaign> AND first_dialed_at::date =
--     today`. Move a lead out at 14:00 and the count drops by one, so the call
--     campaign dials one EXTRA fresh lead. Every email-1 send would raise the
--     effective cap by one, silently.
--
--   * THE FUNNEL REWRITES ITSELF. funnel._where and forecast.build both filter
--     `l.campaign_id = %(cid)s`. As leads succeed and migrate to the drip, the
--     call campaign loses exactly its SUCCESSES - "40 humans reached, 12 emails
--     captured" decays toward "40 humans, 0 emails". That rewrites completed
--     history and breaks the prompt-version comparisons the agent version is
--     pinned to make possible.
--
--   * ATTRIBUTION DIES. Same fault archive.return_due() explicitly refuses to
--     commit: "clearing first_dialed_at would quietly rewrite a completed
--     month's history to improve a future one."
--
-- THE BRIEF'S STATED GOAL IS ALREADY MET WITHOUT MOVING ANYTHING. A lead can
-- only enter a drip by having email 1 sent, which requires has_confirmed_email,
-- and dialer.STAGE_DIALABLE is already `AND NOT l.has_confirmed_email`. The
-- call selector ALREADY skips every lead in a drip. No dialer change at all.
ALTER TABLE leads
  ADD COLUMN drip_campaign_id uuid REFERENCES campaign_configs(campaign_id);

CREATE INDEX leads_drip ON leads (drip_campaign_id)
  WHERE drip_campaign_id IS NOT NULL;

COMMENT ON COLUMN leads.drip_campaign_id IS
  'The DRIP campaign working this lead, or NULL. Set when email 1 is sent; the only entry to a drip. leads.campaign_id is NEVER moved - it stays the CALL campaign that sourced the lead, because it is the daily cap''s counting key and the funnel''s attribution key. Cleared by the archive return, like every other gate.';

-- ============================================================================
-- 2. THE SEQUENCE IS SEAN'S, NOT THE SCHEMA'S. STEPS ARE ROWS.
-- ============================================================================
--
-- Three steps or seven; four days or thirty. Nothing here caps the length.
CREATE TABLE drip_steps (
  step_id     bigserial PRIMARY KEY,
  campaign_id uuid NOT NULL REFERENCES campaign_configs(campaign_id)
                   ON DELETE CASCADE,
  position    int  NOT NULL CHECK (position >= 1),
  -- DELAY FROM emailed_at, ALWAYS. Never from the previous step - see below.
  delay_days  int  NOT NULL CHECK (delay_days >= 0 AND delay_days <= 365),
  subject     text NOT NULL,
  body        text NOT NULL,
  -- SOFT DELETE, because "deleting a step" must not erase the record of who
  -- already received it. The brief's rule: a step already sent is never
  -- re-sent and never re-dated; those who got it KEEP the record, others skip
  -- it. A hard delete would cascade email_sends away and silently renumber
  -- what everyone received.
  deleted_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Position is unique among LIVE steps only; deleted ones keep their old number
-- so the send records still point at something meaningful.
CREATE UNIQUE INDEX drip_steps_position ON drip_steps (campaign_id, position)
  WHERE deleted_at IS NULL;

COMMENT ON TABLE drip_steps IS
  'One row per step of a drip sequence. ROWS, not columns, so the sequence length is Sean''s and not the schema''s. Soft-deleted so a step''s send records survive it.';

COMMENT ON COLUMN drip_steps.delay_days IS
  'Days after leads.emailed_at - the FIRST send - never after the previous step. Chaining lets the schedule drift; anchoring does not. This is why emailed_at is write-once and mark_emailed() is a no-op on a second call: a restamp would move EVERY scheduled send. Break 18 guards that.';

-- ============================================================================
-- 3. SENDS ARE RECORDED PER STEP PER LEAD
-- ============================================================================
--
-- "Has this lead had step 3" has to be a FACT in the database, not inferred
-- from a count - otherwise deleting a step silently renumbers what everyone
-- received.
--
-- THIS IS NOT email_audit. That table logs every ATTEMPT including refusals and
-- failures, which is what you read when asking "why did nothing go out". This
-- table has one row per email that actually WENT, and it owns that send's click
-- token.
--
-- step_id IS NULLABLE on purpose: email 1 can be sent to a lead when no drip
-- campaign exists at all (the drip is optional, and "build ONE drip to start"
-- means for a while there is none). A NULL step_id means "email 1, sent outside
-- any sequence" - the send still gets a row and still gets a token, so click
-- attribution works uniformly whether or not a drip is running.
CREATE TABLE email_sends (
  send_id     bigserial PRIMARY KEY,
  lead_id     uuid   NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
  step_id     bigint REFERENCES drip_steps(step_id),
  -- Which email in the lead's sequence this was: 1 for email 1, 2 for the
  -- second, and so on. Denormalised deliberately - it stays true after a step
  -- is soft-deleted or the sequence is reordered, which is exactly when a
  -- position read live off drip_steps would start lying about history.
  seq         int    NOT NULL CHECK (seq >= 1),
  to_email    text   NOT NULL,
  subject     text,
  sent_at     timestamptz NOT NULL DEFAULT now(),
  sent_by     text,
  -- ⚠️ ONE TOKEN PER SEND, NOT PER LEAD. This is what makes clicks attributable
  -- to the step that produced them: if step 1 pulls every click the follow-ups
  -- are noise, and if step 3 does the opener needs rewriting. A per-lead count
  -- cannot tell those apart. Replaces leads.click_token.
  click_token text UNIQUE
);

CREATE INDEX email_sends_lead ON email_sends (lead_id, seq);
CREATE UNIQUE INDEX email_sends_one_per_step
  ON email_sends (lead_id, step_id) WHERE step_id IS NOT NULL;

COMMENT ON TABLE email_sends IS
  'One row per campaign email that actually went out, per step per lead. NOT email_audit, which logs attempts and refusals. Owns the send''s click token, so a click attributes to the step that produced it.';

-- ⚠️ TWO DIFFERENT ANCHORS, AND ANYONE READING ONE WILL ASSUME THE OTHER.
--
--   THE SCHEDULE anchors to leads.emailed_at - the first send. Every step's
--   delay_days is measured from there, so the sequence cannot drift.
--
--   CLICK TIMING anchors to THIS SEND. "clicked 47m after send" on step 3 has
--   to mean 47 minutes after step 3 went out. Measured from emailed_at it would
--   report every later click as "11 days after send", which is true of the
--   sequence and useless about the email.
ALTER TABLE email_clicks
  ADD COLUMN send_id bigint REFERENCES email_sends(send_id);

CREATE INDEX email_clicks_send ON email_clicks (send_id);

COMMENT ON COLUMN email_clicks.send_id IS
  'Which SEND was clicked - the step attribution. minutes_since_sent is measured from that send''s sent_at, NOT from leads.emailed_at: the schedule anchors to the first send, click timing anchors to this one.';

-- Carry the existing per-lead tokens onto their step-1 send rows, so live
-- tracked links keep working. seq=1, step_id NULL: these predate any drip.
INSERT INTO email_sends (lead_id, step_id, seq, to_email, subject, sent_at,
                         sent_by, click_token)
SELECT l.lead_id, NULL, 1, coalesce(l.dm_email, ''), NULL,
       coalesce(l.emailed_at, now()), l.emailed_by, l.click_token
  FROM leads l
 WHERE l.click_token IS NOT NULL;

-- Point existing clicks at the send they must have come from.
UPDATE email_clicks c
   SET send_id = s.send_id
  FROM email_sends s
 WHERE s.lead_id = c.lead_id AND c.send_id IS NULL;

ALTER TABLE leads DROP COLUMN click_token;

-- ============================================================================
-- 4. WHAT A DRIP CAMPAIGN OWNS
-- ============================================================================
--
-- is_running IS THE SWITCH for a drip, exactly as it is for a call campaign -
-- and one_running_campaign is already scoped to type='call' (migration 028), so
-- many drips may run at once. Starting a drip is what turns its sending on.
--
-- after_last_step: what happens when the sequence is exhausted. archive is the
-- brief's answer (reason no_reply); 'hold' leaves the lead for a person, which
-- is what you want while a new sequence is being tuned.
ALTER TABLE campaign_configs
  ADD COLUMN after_last_step text NOT NULL DEFAULT 'archive'
    CHECK (after_last_step IN ('archive', 'hold'));

COMMENT ON COLUMN campaign_configs.after_last_step IS
  'Drip campaigns only. archive = archive(no_reply) once the last step is sent, which is the brief''s answer and keeps the "everything terminates" property. hold = leave it for a person.';
