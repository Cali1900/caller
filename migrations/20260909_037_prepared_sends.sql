-- email_sends.sent_at BECOMES NULLABLE: a row is created when an email is
-- PREPARED and stamped when it actually goes.
--
-- WHY. Email 1's draft is rendered and STORED at capture time, and break 60
-- pins that the Copy button and Send now produce the same bytes. So the
-- tracked link has to exist before the send does - which means the token, which
-- means the send row. Migration 036 declared sent_at NOT NULL DEFAULT now(),
-- which would have forced either a fake send time on a draft nobody had sent
-- yet, or a second token scheme for email 1 alongside the per-send one.
--
-- Both are worse than letting the row mean "prepared". So:
--
--   sent_at IS NULL   prepared. A draft exists, its token is live, and a click
--                     on it records minutes_since_sent = NULL, which is exactly
--                     right: there is no send to measure from.
--   sent_at SET       it went. Click timing anchors here.
--
-- That also makes the click-timing anchor honest for a link Sean copied out of
-- the CRM and pasted somewhere by hand: the click is real, the "N minutes after
-- send" is unknown, and NULL says so rather than reporting zero.
ALTER TABLE email_sends ALTER COLUMN sent_at DROP NOT NULL;
ALTER TABLE email_sends ALTER COLUMN sent_at DROP DEFAULT;
ALTER TABLE email_sends ADD COLUMN prepared_at timestamptz NOT NULL DEFAULT now();

COMMENT ON TABLE email_sends IS
  'One row per campaign email PREPARED for a lead, per step. sent_at is NULL until it actually goes. NOT email_audit, which logs every attempt including refusals. Owns the send''s click token, so a click attributes to the step that produced it.';

COMMENT ON COLUMN email_sends.sent_at IS
  'When it actually went, or NULL if only prepared. email_clicks.minutes_since_sent is measured from here, so a click on a prepared-but-unsent link correctly records NULL rather than zero.';
