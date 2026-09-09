-- MANUAL REPLY DETECTION. Sean reads every reply at this volume, so the guard
-- is a person ticking a box - and that is enough. When automatic ingest is
-- eventually built it becomes a SECOND WRITER to the same field, not a
-- replacement, so nothing downstream has to change.
--
-- reply_note is what they actually said, in Sean's words. Kept beside
-- replied_at rather than only on the timeline so it can be edited and read
-- back without scrolling a history.
ALTER TABLE leads ADD COLUMN reply_note text;
ALTER TABLE leads ADD COLUMN replied_by text;

COMMENT ON COLUMN leads.replied_at IS
  'When they replied. Written by stages.record_reply() - today from the "I got '
  'a reply" checkbox, later also by automatic ingest. THE AUTO-SEND GATE READS '
  'THIS: a lead that replied is never auto-contacted again.';
COMMENT ON COLUMN leads.replied_by IS
  'Who recorded it: an operator name, or auto:<source> when ingest lands.';
