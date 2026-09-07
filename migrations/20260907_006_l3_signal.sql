-- Phase 5, second migration (005 was already applied, and an applied
-- migration is never edited).
--
-- What L3 exists to learn: did the decision maker actually SEE the email.
-- Storing it as a column rather than leaving it in the analysis blob because
-- it is a SELECTABLE fact - it decides who is worth calling again, and in
-- phase 6 it is one of the three gates that let L4 fire at all. Capturing
-- what L3 discovers is phase 5's job; the gate that reads it is not.

ALTER TABLE leads ADD COLUMN dm_saw_email boolean;
ALTER TABLE leads ADD COLUMN best_next_step text;

COMMENT ON COLUMN leads.dm_saw_email IS
  'Did the decision maker see our email, per an L3 call. NULL = never asked. One of the three phase-6 gates that permit an L4 demo call.';

CREATE INDEX leads_saw_email ON leads (dm_saw_email) WHERE dm_saw_email IS TRUE;
