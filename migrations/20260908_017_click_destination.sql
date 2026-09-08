-- WHERE A TRACKED CLICK REDIRECTS TO, per lead.
--
-- The first cut hardcoded 'https://counselorai.io/#letter' and rewrote only
-- that exact string. The email copy is OPERATOR-EDITABLE - Sean had already
-- changed the link to 'https://counselorai.io/' - so the rewrite matched
-- nothing and silently produced an untracked draft. A constant that has to
-- agree with editable text is a constant that will disagree with it.
--
-- Now: any counselorai.io link in the copy is rewritten, and the URL it
-- replaced is stored here so the redirect sends the recipient exactly where
-- the copy pointed. Editing the copy changes the destination; nothing has to
-- be kept in sync by hand.

ALTER TABLE leads ADD COLUMN click_destination text;

COMMENT ON COLUMN leads.click_destination IS
  'The URL the tracked link replaced. The redirect target for this lead.';
