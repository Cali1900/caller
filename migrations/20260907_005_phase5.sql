-- Phase 5: L2 and L3.
-- Forward-only. Never edit an applied migration.

-- ===========================================================================
-- replied_at
--
-- NOTHING SETS THIS YET, and that is deliberate. The coming email automation
-- (sequenced send from demandcounselor.com, ~15 min after a confirmed
-- capture) must STOP DEAD on any reply. The guard belongs in the selection
-- query from the start: adding it later would mean changing the dialer at the
-- same moment the sender arrives, which is exactly the rewrite this column
-- exists to avoid.
--
-- Until the sender exists this is always NULL and the guard is inert.
-- ===========================================================================
ALTER TABLE leads ADD COLUMN replied_at timestamptz;

COMMENT ON COLUMN leads.replied_at IS
  'When they replied to our email. A reply STOPS the follow-up call dead - the L3 selection query excludes it. Nothing sets this yet; the coming sender will.';

CREATE INDEX leads_replied ON leads (replied_at) WHERE replied_at IS NOT NULL;

-- ===========================================================================
-- Stage transitions are recorded on the lead so the timeline can show the
-- ladder, and so "why is this lead at L3" is answerable without inferring it
-- from activity text.
-- ===========================================================================
ALTER TABLE leads ADD COLUMN stage_changed_at timestamptz;

-- emailed_at / emailed_by already exist (phase 0 schema). emailed_by carries
-- WHO sent it: an operator name today, 'auto:<domain>' once the sequencer
-- runs. Same column, same transition function - that is the seam.
COMMENT ON COLUMN leads.emailed_by IS
  'Operator name for a manual send, or auto:<domain> once the sequencer runs. Both go through stages.mark_emailed().';
