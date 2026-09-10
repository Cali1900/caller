-- THE ASSIGNMENT COLUMNS GO. Membership is derived from status (043).
--
-- ⚠️ SEND HISTORY IS NOT TOUCHED. email_sends and email_clicks are what make
-- re-entry resume instead of restart: a lead can go emailed -> engaged ->
-- emailed and pick up where it left off, because ALREADY_SENT_STOP matches on
-- (lead_id, step_id) and those rows persist through the gap. Dropping them would
-- silently re-send every step to every returning lead.

-- 1. A NEW STATUS FOR IMPORTED LEADS. Not 'emailed' - nothing has been emailed
--    to them and emailed_at is NULL, so reusing the label would be one word
--    meaning two things. Not 'new' either: 'new' means "waiting to be dialled"
--    and an imported lead has no phone.
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_status_check CHECK (status = ANY (ARRAY[
  'new','queued','dialing','completed','callback','no_answer','email_path',
  'max_attempts','failed','paused','human_review','demo_pending','dnc',
  'emailed','engaged','demo_booked','won','lost','lost_no_response',
  'bad_email','archived','imported']));

UPDATE leads SET status = 'imported'
 WHERE lead_source = 'import' AND status = 'new' AND phone_e164 IS NULL;

-- 2. THE MAILBOX MOVES ONTO THE SEND ROW. The per-mailbox caps used to find the
--    address through coalesce(drip_campaign_id, campaign_id) - a join the gate
--    deletes, and a guess in any case: it asked which campaign the lead belongs
--    to NOW rather than which address the mail actually went from.
ALTER TABLE email_sends ADD COLUMN from_email text;
UPDATE email_sends es SET from_email = c.sender_email
  FROM leads l JOIN campaign_configs c ON c.campaign_id = l.campaign_id
 WHERE l.lead_id = es.lead_id AND es.from_email IS NULL;
UPDATE email_sends SET from_email = 'info@counselorai.io' WHERE from_email IS NULL;

COMMENT ON COLUMN email_sends.from_email IS
  'The mailbox this went from. The hourly and daily caps count on it, because '
  'reputation belongs to the ADDRESS and the send row is the only place that '
  'fact is true forever.';

-- 3. THE ASSIGNMENT COLUMNS THEMSELVES.
ALTER TABLE leads DROP COLUMN drip_campaign_id;
ALTER TABLE leads DROP COLUMN drip_entered_at;
ALTER TABLE campaign_configs DROP COLUMN default_drip_id;

