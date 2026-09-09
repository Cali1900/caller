-- ARCHIVE IS A STATUS, NOT A TABLE.
--
-- Every lead must be in exactly one place: worked by the machine, worked by
-- Sean, or resting. Archive is resting, and it terminates - a lead comes back
-- to the pool six months later, or it does not come back at all.
--
-- THE REASON IS THE POINT. Six months on, a firm that ran out of no-answers
-- is a completely different prospect from one that said no. A single
-- 'archived' status without the reason loses that distinction permanently,
-- and it is not recoverable from anything else on the row.
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_status_check CHECK (status IN (
  'new', 'queued', 'dialing', 'completed', 'callback', 'no_answer',
  'email_path', 'max_attempts', 'failed', 'paused', 'human_review',
  'demo_pending', 'dnc', 'emailed', 'engaged', 'demo_booked', 'won', 'lost',
  'lost_no_response', 'bad_email', 'archived'));

ALTER TABLE leads
  ADD COLUMN archived_at    timestamptz,
  ADD COLUMN archive_reason text
    CONSTRAINT leads_archive_reason_check CHECK (archive_reason IN (
      'max_attempts', 'refused', 'no_reply', 'bad_email',
      'unsubscribed', 'manual')),
  ADD COLUMN returns_at     timestamptz;

-- The nightly sweep reads exactly this.
CREATE INDEX leads_returns_at_idx ON leads (returns_at)
  WHERE status = 'archived' AND returns_at IS NOT NULL;

COMMENT ON COLUMN leads.returns_at IS
  'archived_at + 6 months. The nightly sweep returns the lead to the pool past this. Returning NEVER clears suppression or email_do_not_send - those are keyed on the phone and the address, not the lead, and they outlive everything.';

-- THE EMAIL DO-NOT-SEND LIST.
--
-- The third exclusion list, and it must never be merged with the other two.
-- Suppression is a PHONE and a compliance obligation. Cooling off
-- (lost_no_response) is a LEAD and a business judgement that expires. This is
-- an ADDRESS and a fact about a mailbox: it bounced hard, or a person asked
-- to be removed. None of the three implies either of the others.
--
-- It exists now because archive made it load-bearing. bad_email was only a
-- STATUS, and the sweep rewrites status to 'new' - so a hard-bounced address
-- would have come back out of archive fully emailable, with nothing on the
-- row remembering why it should not be. Keyed on the address so it survives
-- the lead being returned, re-uploaded, or deduplicated.
CREATE TABLE email_do_not_send (
  email      text PRIMARY KEY,
  reason     text NOT NULL,
  source     text,
  created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE email_do_not_send IS
  'Addresses that must never be emailed: hard bounces and unsubscribes. Keyed on the address, NOT the lead, so it survives archive, return, re-upload and dedup. Never merged with suppression (phone/compliance) or lost_no_response (lead/business).';
