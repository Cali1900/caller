-- The email copy is a PROPERTY OF THE CAMPAIGN, like everything else it owns.
-- Two campaigns running different copy is most of the reason to have a second
-- campaign at all, so hardcoding the template defeated the model.
--
-- Placeholders are substituted at render time:
--   {{first_name}}      the decision maker's first name
--   {{gatekeeper_name}} whoever answered, when she gave it
--   {{company}}         the firm
--   {{time_of_day}}     "this morning" / "this afternoon", from the call time
--   {{sender_name}}     the campaign's sender name
--   {{footer}}          the campaign's footer line
--
-- The last two are placeholders rather than appended text so that editing the
-- sender still flows into the copy without re-editing every template.

ALTER TABLE campaign_configs ADD COLUMN subject_with_name text;
ALTER TABLE campaign_configs ADD COLUMN subject_without   text;
ALTER TABLE campaign_configs ADD COLUMN body_with_name    text;
ALTER TABLE campaign_configs ADD COLUMN body_without      text;

-- Seed every existing campaign with the copy that was hardcoded, so nothing
-- changes for anyone until they edit it.
UPDATE campaign_configs SET
  subject_with_name = COALESCE(subject_with_name,
    'Following up — spoke with your front desk'),
  subject_without = COALESCE(subject_without,
    'Quick follow-up from {{time_of_day}}'),
  body_with_name = COALESCE(body_with_name,
'Hi {{first_name}},

{{gatekeeper_name}} at your front desk pointed me your way — she said you''re
the one who handles demand letters.

We built CounselorAI for PI firms — it drafts the full demand package
from the case records, with citations verified against a closed
library of published opinions. Most firms spend six to eight hours on
one; this takes about thirty minutes.

First one''s free on a real file, no card. If it''s not better than what
you''d have sent, you''ve lost fifteen minutes.

Sample demand, redacted: https://counselorai.io/#letter

Worth a look?

{{sender_name}}
CounselorAI

Reply "unsubscribe" and I''ll take you off the list.
{{footer}}
'),
  body_without = COALESCE(body_without,
'Hi {{first_name}},

I called your office {{time_of_day}} and your front desk pointed me your way
on demand letters.

We built CounselorAI for PI firms — it drafts the full demand package
from the case records, with citations verified against a closed
library of published opinions. Most firms spend six to eight hours on
one; this takes about thirty minutes.

First one''s free on a real file, no card. If it''s not better than what
you''d have sent, you''ve lost fifteen minutes.

Sample demand, redacted: https://counselorai.io/#letter

Worth a look?

{{sender_name}}
CounselorAI

Reply "unsubscribe" and I''ll take you off the list.
{{footer}}
');

ALTER TABLE campaign_configs ALTER COLUMN subject_with_name SET NOT NULL;
ALTER TABLE campaign_configs ALTER COLUMN subject_without   SET NOT NULL;
ALTER TABLE campaign_configs ALTER COLUMN body_with_name    SET NOT NULL;
ALTER TABLE campaign_configs ALTER COLUMN body_without      SET NOT NULL;
