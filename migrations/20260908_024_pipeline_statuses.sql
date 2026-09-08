-- THE SEVEN PIPELINE STATUSES.
--
-- Until now `status` described what the DIALER did (new, callback, no_answer).
-- These describe where the lead is in the FUNNEL, which is what Sean actually
-- works from - and what the forecast should weigh, rather than deriving
-- "engaged" from clicked-or-replied and hoping the derivation stays true.
--
--   emailed            email 1 sent, drip running
--   engaged            clicked or replied - needs a person
--   demo_booked        a demo is in the diary
--   won                became a customer
--   lost               an explicit no
--   lost_no_response   the drip finished with nothing
--   bad_email          bounced - RECOVERABLE, we likely have the right firm
--                      and the wrong address
--
-- `lost` and `lost_no_response` are separate on purpose: a decision somebody
-- made and an absence of one are not the same event, and only one of them is
-- worth revisiting in ninety days.

ALTER TABLE leads DROP CONSTRAINT leads_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_status_check CHECK (status = ANY (ARRAY[
    -- dialer states
    'new', 'queued', 'dialing', 'completed', 'callback', 'no_answer',
    'email_path', 'max_attempts', 'failed', 'paused',
    -- attention
    'human_review', 'demo_pending',
    -- compliance
    'dnc',
    -- pipeline
    'emailed', 'engaged', 'demo_booked', 'won', 'lost', 'lost_no_response',
    'bad_email'
]::text[]));

COMMENT ON COLUMN leads.status IS
  'Dialer state, attention, or pipeline stage. The SYSTEM sets these and the '
  'operator overrules it - every hand change lands on the timeline.';
