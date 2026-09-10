-- ⚠️ PHANTOM SENDS: rows marked sent, with no recipient, that never went.
--
-- ============================================================================
-- WHAT WROTE THEM - migration 036's OWN backfill
-- ============================================================================
--
--   INSERT INTO email_sends (lead_id, step_id, seq, to_email, subject, sent_at,
--                            sent_by, click_token)
--   SELECT l.lead_id, NULL, 1, coalesce(l.dm_email, ''), NULL,
--          coalesce(l.emailed_at, now()), l.emailed_by, l.click_token
--     FROM leads l WHERE l.click_token IS NOT NULL;
--
-- It carried per-lead click tokens onto send rows so live tracked links kept
-- working. For a lead that had a TOKEN but had never been EMAILED, both
-- coalesces fabricated a record of something that never happened:
--
--   coalesce(l.dm_email, '')      -> '' satisfies NOT NULL and means nothing
--   coalesce(l.emailed_at, now()) -> stamps it SENT, at migration time
--
-- One INSERT ... SELECT, so now() is identical for every fabricated row - which
-- is why two rows share a microsecond and look like one operation. They were.
--
-- Nothing was transmitted: no email_audit row for either lead, no sent_by, no
-- recipient, and both leads still have emailed_at NULL. The rows claim a send
-- the leads themselves do not.
--
-- Row 3 also linked to a drip step, so the roster read "1 sent, sequence
-- finished" for a lead on a four-step drip that has had nothing.
--
-- ============================================================================
-- WHY THIS IS A CONSTRAINT AND NOT A HANDLER FIX
-- ============================================================================
--
-- NOT NULL was already on to_email and did not stop this: '' is not NULL. A
-- writer that must satisfy NOT NULL and has nothing to write will write the
-- empty string, and every reader then has to know that '' means absent. The
-- CHECK removes the option.
--
-- Same for sent_at. It was `NOT NULL DEFAULT now()` in 036, so ANY insert that
-- did not mention it declared a send - the default did the lying. 037 made it
-- nullable, which fixed new rows and left the fabricated ones behind. Requiring
-- sent_by alongside makes "sent" a thing somebody or something DID, rather than
-- a column that filled itself in.
DELETE FROM email_sends
 WHERE sent_at IS NOT NULL
   AND btrim(coalesce(to_email, '')) = ''
   AND sent_by IS NULL
   -- never delete a row a click points at: the click is evidence of a real
   -- open, whatever the send row claims about itself.
   AND NOT EXISTS (SELECT 1 FROM email_clicks c WHERE c.send_id = email_sends.send_id);

-- A PREPARED row may exist before it goes; it may NEVER exist without someone
-- to send it to.
ALTER TABLE email_sends
  ADD CONSTRAINT email_sends_has_recipient
      CHECK (btrim(to_email) <> ''),
-- SENT IS AN ACT, AND AN ACT HAS AN ACTOR. 'operator', or auto:drip:<domain>.
  ADD CONSTRAINT email_sends_sent_is_attributed
      CHECK (sent_at IS NULL OR sent_by IS NOT NULL);

COMMENT ON COLUMN email_sends.to_email IS
  'The recipient. NEVER empty - CHECK email_sends_has_recipient. A send row '
  'with no address is not a send, and NOT NULL alone did not stop one: '
  'migration 036 wrote coalesce(dm_email, '''') to satisfy it.';
COMMENT ON COLUMN email_sends.sent_by IS
  'Who sent it: ''operator'' or auto:drip:<domain>. REQUIRED whenever sent_at '
  'is set - CHECK email_sends_sent_is_attributed - so "sent" is something that '
  'was done rather than a column default.';
