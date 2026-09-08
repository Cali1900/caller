-- EVERY EMAIL REFUSAL IS AUDITED, never silent.
--
-- The same rule as dial_audit: a silent refusal is how you spend an hour
-- asking why nothing sent. This is the row that answers it.

CREATE TABLE email_audit (
    audit_id    bigserial PRIMARY KEY,
    lead_id     uuid REFERENCES leads(lead_id) ON DELETE SET NULL,
    to_email    text,
    outcome     text NOT NULL,
    detail      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX email_audit_lead ON email_audit (lead_id, created_at DESC);
CREATE INDEX email_audit_outcome ON email_audit (outcome, created_at DESC);

COMMENT ON TABLE email_audit IS
  'One row per send ATTEMPT, sent or refused. A silent refusal is how you '
  'spend an hour asking why nothing went out.';
