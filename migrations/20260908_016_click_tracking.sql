-- CLICK TRACKING. Sean sends every email by hand; the app records what happens
-- after. The sample link in a draft is rewritten to a tracked URL that logs the
-- click and 302s to the real page, so the recipient sees no difference.
--
-- NO OPEN TRACKING. Apple Mail Privacy Protection pre-loads pixels, so an
-- "open" fires whether or not a human looked. Clicks and replies only.

-- The token lives on the lead, not in the URL path, and is generated once.
-- One token per lead, not per email: the drip sends up to four emails and a
-- click is a click - which email it came from is answered by the timing, and
-- a per-email token would let anyone who has one enumerate the others.
ALTER TABLE leads ADD COLUMN click_token text UNIQUE;

CREATE TABLE email_clicks (
    click_id        bigserial PRIMARY KEY,
    lead_id         uuid NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    clicked_at      timestamptz NOT NULL DEFAULT now(),

    -- THE NUMBER SEAN ACTUALLY WANTS. Stored, not computed on read: emailed_at
    -- is write-once today, but a stored interval cannot be retroactively
    -- changed by anything, and "47m after send" is a fact about a moment.
    -- NULL when the lead had no emailed_at - a click before any recorded send
    -- is a real possibility (a forwarded mail) and must not be silently zero.
    minutes_since_sent  integer,

    user_agent      text,
    ip              inet,
    UNIQUE (lead_id, clicked_at)
);

CREATE INDEX email_clicks_lead ON email_clicks (lead_id, clicked_at DESC);

COMMENT ON TABLE email_clicks IS
  'One row per click. He may click twice - that is data, not a duplicate.';
COMMENT ON COLUMN email_clicks.minutes_since_sent IS
  'Whole minutes between leads.emailed_at and this click. NULL if never sent.';
