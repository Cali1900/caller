-- The from-address is a CHOICE FROM A LIST now, not free text: Brevo only
-- delivers from a sender verified on the account, so a typed address is a
-- config that cannot send.
--
-- Existing campaigns carry 'sean@counselorai.io', which is neither offered nor
-- verified. Point them at the default. Safe to rewrite: nothing has ever been
-- sent from these campaigns - drafts are generated and sent BY HAND, and the
-- sender is only the From: shown on the draft.
--
-- Anything already sent is unaffected: email_drafts.to_email and the timeline
-- record what actually happened, and neither is touched here.

UPDATE campaign_configs
   SET sender_email = 'info@counselorai.io'
 WHERE sender_email NOT IN ('info@counselorai.io', 'sean@demandcounselor.com');
