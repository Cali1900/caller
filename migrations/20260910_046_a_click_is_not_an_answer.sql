-- ⚠️ A CLICK GETS ITS OWN STATUS. `engaged` means they REPLIED.
--
-- Since click tracking was built the standing rule has been that a click is
-- INTEREST, NOT AN ANSWER - only a reply stops a sequence. clicks.record()
-- nevertheless advanced the lead to `engaged`, which conflated the two: one
-- label meaning "they answered" and "they read the sample".
--
-- That was harmless while status governed dialling. Now that status governs
-- SENDING it decides whether a warm lead keeps hearing from us, and the
-- conflation produced the worst version of this feature: A FIRM READS THE SAMPLE
-- AND THE FOLLOW-UP STOPS.
--
-- `clicked` sits between `emailed` and `engaged` in the pipeline, so the ladder
-- stays forward-only: a click promotes emailed -> clicked, a reply promotes
-- either of them to engaged. replied_at still stops everything, separately and
-- unchanged - REPLIED_STOP is not a status check.
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_status_check CHECK (status = ANY (ARRAY[
  'new','queued','dialing','completed','callback','no_answer','email_path',
  'max_attempts','failed','paused','human_review','demo_pending','dnc',
  'emailed','clicked','engaged','demo_booked','won','lost','lost_no_response',
  'bad_email','archived','imported']));

-- Leads whose only "engagement" was a click are re-labelled: they were never
-- answers. A lead with replied_at is a real reply and stays `engaged`.
UPDATE leads SET status = 'clicked'
 WHERE status = 'engaged' AND replied_at IS NULL
   AND EXISTS (SELECT 1 FROM email_clicks c WHERE c.lead_id = leads.lead_id);
