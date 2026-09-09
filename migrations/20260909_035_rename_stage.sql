-- RENAME leads.stage -> leads.has_confirmed_email, AND MAKE IT A BOOLEAN.
--
-- THE NAME IS WHAT HID A REAL BUG. leads.stage held exactly two values ('L1',
-- 'L2') and therefore exactly one bit: has a confirmed email been captured. It
-- was named after a four-rung ladder (L1->L2->L3->L4) that no longer exists -
-- migration 027 narrowed the constraint to two values, correctly.
--
-- Because "stage" does not sound like state a lead's RETURN FROM ARCHIVE
-- should clear, archive.return_due() did not clear it. Nothing in the codebase
-- ever wrote 'L1' back, and dialer.STAGE_DIALABLE was "AND l.stage = 'L1'", so
-- every lead archived from L2 came back to the pool permanently undialable.
-- Fixed in migration 032; this removes the reason it was invisible.
--
--     AND l.stage = 'L1'          reads as "which rung is it on"
--     AND NOT l.has_confirmed_email    reads as what it actually tests
--
-- 'stage' MEANT THREE DIFFERENT THINGS in this codebase, which is the other
-- half of the confusion. Only this column is renamed:
--
--   leads.stage            the L1/L2 bit                -> RENAMED HERE
--   api/forecast.py,       funnel stage (emailed ->      -> untouched, correct
--   api/pipeline.py        engaged -> demo_booked)
--   api/prompts.py,        which agent prompt to dial    -> untouched, correct
--   api/retell.py          with
--   activity.stage         a HISTORY label, holds the    -> untouched: those
--                          literal 'L2' on past rows        are past records
--
-- BOOLEAN, not a renamed text column. Two magic strings under an honest name
-- would be worse than the old name: the point is that the column carries one
-- bit and now says so. NOT NULL DEFAULT false, so a new lead has not got a
-- confirmed email, which is the safe reading.

ALTER TABLE leads ADD COLUMN has_confirmed_email boolean NOT NULL DEFAULT false;

-- Lockstep verified before writing this: 1086 rows at L1 with
-- dm_email_confirmed NULL, 1 row at L2 with TRUE, zero disagreement.
UPDATE leads SET has_confirmed_email = (stage = 'L2');

-- stage_changed_at only ever recorded the L1->L2 move, which is this one event.
ALTER TABLE leads RENAME COLUMN stage_changed_at TO email_confirmed_at;

DROP INDEX IF EXISTS leads_stage;
CREATE INDEX leads_confirmed_email ON leads (has_confirmed_email, status);

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_stage_check;
ALTER TABLE leads DROP COLUMN stage;

-- AND THE THIRD stage-NAMED COLUMN, WHICH NOTHING HAS EVER READ.
-- leads.stage_attempts was declared in migration 001 and never referenced
-- again: no production code, no test, no migration writes or reads it. All
-- 1,087 live rows hold 0, its NOT NULL DEFAULT, so dropping it loses no
-- information - verified before writing this.
--
-- Same fault as the ten inert `settings` rows dropped in migration 015 and
-- `agent_l3_version` in 025: a column that LOOKS like state and is not. It is
-- dropped here rather than left because the whole point of this migration is
-- that a reader should be able to trust what the stage-named columns mean, and
-- one of them meaning nothing at all defeats that.
ALTER TABLE leads DROP COLUMN stage_attempts;

COMMENT ON COLUMN leads.has_confirmed_email IS
  'TRUE once a CONFIRMED email has been captured (was stage=''L2''). The dialer refuses these: at that point we owe the firm an email and have not sent it, so calling asks a question we are about to answer ourselves. Cleared by the archive return - see api/archive.py, and migration 032 for the bug the old name hid.';

COMMENT ON COLUMN leads.email_confirmed_at IS
  'When has_confirmed_email became true. Was stage_changed_at, which only ever recorded that one transition.';
