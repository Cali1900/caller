-- THE REPLY IS A GATE TOO, AND THE RETURN NOW CLEARS IT.
--
-- dialer.REPLIED_GUARD is "AND l.replied_at IS NULL". replied_at survived the
-- archive return, so a lead that replied once - "not interested" - archived as
-- refused, and rested six months came back to the pool and COULD NEVER BE
-- DIALED AGAIN. Identical shape to the `stage` bug fixed in migration 032, in
-- the same function, and hidden the same way: no test sets replied_at on a
-- lead that is archived and returned.
--
-- Proved against the real selection query before changing anything: with
-- stage='L1' so the stage gate cannot be the cause, the returned lead's
-- candidate set is empty.
--
-- WHY CLEARING IT IS CORRECT, and it is the same reasoning as emailed_at:
--
--   Six months on, a firm that said "not interested" is a legitimate prospect
--   again - that is the entire premise of archive_reason='refused' returning
--   to the pool at all. If a reply were meant to be permanent, the lead would
--   never have been archived with a return date.
--
--   It defeats NO exclusion list. suppression is keyed on the PHONE and
--   email_do_not_send on the ADDRESS; neither is keyed on the lead, and the
--   sweep still touches neither. Someone who asked to be removed stays
--   removed. Someone who merely was not interested in March gets one more
--   call in September.
--
-- THE FACT SURVIVES THE GATE. replied_at, reply_note and replied_by are
-- snapshotted into archived_contacts alongside the send record, so "they told
-- us no in March, and here is what they said" is still on the lead when it
-- comes back - which is exactly what someone needs before dialing it again.

ALTER TABLE archived_contacts
  ADD COLUMN replied_at timestamptz,
  ADD COLUMN reply_note text,
  ADD COLUMN replied_by text;

COMMENT ON COLUMN archived_contacts.replied_at IS
  'What replied_at said just before the return cleared it. HISTORY, never a gate - the dialer reads leads.replied_at, not this. Kept because someone about to re-dial a returned firm needs to know it said no once, and what it said.';
