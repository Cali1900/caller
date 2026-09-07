-- Phase 3: scoring and the digest.
-- Forward-only. Never edit an applied migration.

-- ===========================================================================
-- Cost is already in the Retell payload - capture it rather than estimate it.
-- Measured 2026-09-07 from real calls (unit prices are cents per SECOND):
--   claude_5_sonnet        0.1333333 -> 8.00 c/min
--   gpt_4_1_high_priority  0.1125    -> 6.75 c/min
--   retell_voice_engine    0.0916667 -> 5.50 c/min
--   platform_tts (Rita)    0.025     -> 1.50 c/min
--   elevenlabs_tts_03_2026 0.0666667 -> 4.00 c/min
--   us_twilio_telephony    0.025     -> 1.50 c/min
-- Plus a flat 1.5c per call for the post-call analysis pass, and an
-- llm_token_surcharge that appeared only on the 8,047-char prompt.
-- ===========================================================================
ALTER TABLE calls ADD COLUMN cost_cents numeric(10,4);
ALTER TABLE calls ADD COLUMN cost_breakdown jsonb;

COMMENT ON COLUMN calls.cost_cents IS
  'combined_cost from Retell, in CENTS. Measured, not estimated.';

CREATE INDEX calls_cost ON calls (created_at DESC) WHERE cost_cents IS NOT NULL;

-- ===========================================================================
-- Scoring runs on EVERY call, so it needs its own failure record. A scoring
-- failure must never break the call flow, which means it cannot raise - so
-- without this table a persistent scorer outage would be invisible.
-- ===========================================================================
ALTER TABLE call_scores ADD COLUMN score_error text;
ALTER TABLE call_scores ADD COLUMN input_tokens int;
ALTER TABLE call_scores ADD COLUMN output_tokens int;
ALTER TABLE call_scores ADD COLUMN cost_cents numeric(10,4);

-- outcome_score/agent_score are NOT NULL, so a failed scoring attempt cannot
-- be recorded in call_scores at all. Track attempts separately.
CREATE TABLE score_attempts (
    call_id     text PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    attempts    int NOT NULL DEFAULT 0,
    last_error  text,
    last_try_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX score_attempts_failed ON score_attempts (last_try_at)
    WHERE last_error IS NOT NULL;

-- ===========================================================================
-- digests - one email per day. Recorded so a re-run cannot double-send, and
-- so "did it go out" is answerable without reading a mailbox.
-- ===========================================================================
CREATE TABLE digests (
    digest_date date PRIMARY KEY,
    sent_at     timestamptz,
    recipient   text,
    subject     text,
    body        text,
    stats       jsonb,
    send_error  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ===========================================================================
-- alerts - immediate, not batched. Phase 6 (L4) sets status='demo_pending';
-- the PATH is wired now so it is not invented under time pressure later.
-- A verbal yes decays: the target is an invite inside 15 minutes.
-- ===========================================================================
CREATE TABLE alerts (
    id         bigserial PRIMARY KEY,
    lead_id    uuid REFERENCES leads(lead_id) ON DELETE CASCADE,
    kind       text NOT NULL,          -- demo_pending | needs_human
    summary    text NOT NULL,
    detail     text,
    sent_at    timestamptz,
    send_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX alerts_unsent ON alerts (created_at) WHERE sent_at IS NULL;
