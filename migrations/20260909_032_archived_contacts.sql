-- THE RETURN CLEARS THE SEND GATE, AND THE SEND RECORD SURVIVES IT.
--
-- THE BUG THIS FIXES. archive.return_due() put a lead back in the pool and
-- called it "a FRESH prospect", but it never reset `stage`. Nothing in this
-- codebase ever writes stage='L1' - api/stages.py only ever writes 'L2' - and
-- dialer.STAGE_DIALABLE is "AND l.stage = 'L1'". So the three EMAIL-derived
-- archive reasons (no_reply, bad_email, unsubscribed), which can ONLY be
-- reached from L2 because they all require email 1 to have gone out, produced
-- leads that came back to the pool and could never be dialed again. They could
-- not be emailed either: emailed_at survived, and mark_emailed() is write-once,
-- so it returned "already sent" forever. A lead in the pool, looking fresh on
-- screen, unreachable down both wires.
--
-- Every test in tests/test_archive.py builds its lead at stage='L1', which is
-- what made the suppression test properly isolated AND what hid this. Same
-- shape as masked guard #1 in README.md: a fixture value production does not
-- supply at that point.
--
-- WHAT THE RETURN NOW CLEARS, and the rule that decides it:
--
--   A GATE is cleared. A FACT is kept.
--
--   emailed_at / emailed_by   GATE. Blocks mark_emailed() forever.
--   stage                     GATE. Blocks the dialer forever.
--   dm_email_confirmed        GATE. Read by autosend.eligibility(),
--                             drafts.generate_for() and advance_to_l2(). It is
--                             not a fact about the firm - it records that WE
--                             performed a verification, and six months on that
--                             verification is stale even if the address is not.
--                             Clearing it is fail-closed: the address survives
--                             and the re-call re-confirms it.
--
--   dm_email, dm_name, dm_title, website, gatekeeper_name, demands_per_month,
--   notes, tags               FACTS. We paid a call to learn them and six
--                             months does not make them untrue. KEPT.
--
--   first_dialed_at           KEPT, as before: it is the cohort key the funnel
--                             measures against.
--
-- ⚠️  THE RETURN STILL CLEARS NO EXCLUSION LIST. suppression is keyed on the
--     phone, email_do_not_send on the address. Neither is keyed on the lead,
--     both outlive it, and the sweep writes to `leads` and `archived_contacts`
--     and nothing else. Breaks 79 and 80 hold that line.

-- The send record, snapshotted at the moment the return is about to destroy it.
-- "We emailed this firm in September and heard nothing" has to survive, or the
-- reason the lead was archived becomes unreconstructable the instant it returns.
CREATE TABLE archived_contacts (
  contact_id     bigserial PRIMARY KEY,
  lead_id        uuid NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
  company        text,
  dm_name        text,
  dm_email       text,
  dm_email_confirmed boolean,
  emailed_at     timestamptz,
  emailed_by     text,
  click_count    int  NOT NULL DEFAULT 0,
  first_click_minutes int,
  archive_reason text,
  archived_at    timestamptz,
  returned_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX archived_contacts_lead ON archived_contacts (lead_id, returned_at DESC);

COMMENT ON TABLE archived_contacts IS
  'One row per RETURN from archive: what the send record said just before the return cleared it. Written by api/archive.py only, and only on the way out of archive. This is history, never a gate - nothing reads it to decide whether to dial or send.';

COMMENT ON COLUMN archived_contacts.click_count IS
  'Clicks on that send, counted at return time. email_clicks rows themselves are NOT deleted - minutes_since_sent was computed and stored at click time, so the timings stay true after emailed_at is cleared.';
