-- Phase 5c: timezone-from-state, gatekeeper name, draft emails.

-- ===========================================================================
-- The gatekeeper's OWN name. This is what makes the follow-up email reference
-- the call instead of reading as cold outreach - the whole value of having
-- made the call. Never a required ask: the agent takes it if she offers it.
-- ===========================================================================
ALTER TABLE leads ADD COLUMN gatekeeper_name text;

-- ===========================================================================
-- Where the timezone came from, and whether a human should look.
-- Several states span zones (TX, FL, TN, KY, IN, ND, SD, NE, KS, MI, OR, ID);
-- we take the DOMINANT zone and flag the row rather than guess silently. A
-- wrong timezone is a TCPA problem, not a cosmetic one.
-- ===========================================================================
ALTER TABLE leads ADD COLUMN tz_source text;
ALTER TABLE leads ADD COLUMN tz_needs_review boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN leads.tz_source IS
  'csv = explicit column (authoritative override) | state = derived | default = fallback';

CREATE INDEX leads_tz_review ON leads (created_at) WHERE tz_needs_review;

-- ===========================================================================
-- email_drafts - GENERATED, NEVER SENT.
--
-- One draft per lead, edited in place. "Mark as sent" is the EXISTING
-- L2 -> L3 transition (stages.mark_emailed) - there is deliberately no second
-- transition here, and no sending code anywhere in this repo.
-- ===========================================================================
CREATE TABLE email_drafts (
    lead_id      uuid PRIMARY KEY REFERENCES leads(lead_id) ON DELETE CASCADE,
    to_email     text NOT NULL,
    subject      text NOT NULL,
    body         text NOT NULL,
    variant      text NOT NULL,          -- with_name | without_name
    generated_at timestamptz NOT NULL DEFAULT now(),
    edited_at    timestamptz,
    edited_by    text
);

COMMENT ON TABLE email_drafts IS
  'Draft follow-up emails. NOTHING IN THIS REPO SENDS THEM. A person reads, edits, sends, then clicks "mark as sent" which is the L2->L3 transition.';

-- Sender identity is operator-editable (counselorai.io now,
-- demandcounselor.com once warm) - a settings row, not an env var, because
-- an env change needs a container recreate.
INSERT INTO settings (key, value, updated_by) VALUES
    ('sender_email', 'sean@counselorai.io', 'seed'),
    ('sender_name',  'Sean',                'seed'),
    ('sender_company_line', 'CounselorAI LLC · [ADDRESS TBD]', 'seed')
ON CONFLICT (key) DO NOTHING;
