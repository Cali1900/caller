-- EMAIL-ONLY LEADS: a list of firms with addresses, no calls involved.
--
-- ============================================================================
-- 1. A LEAD NO LONGER NEEDS A PHONE OR A TIMEZONE
-- ============================================================================
--
-- Both were NOT NULL because every lead arrived from a call list. An imported
-- lead has an address and no number, and there is nothing to derive a timezone
-- from - nor anything that wants one: the ONLY consumers of leads.timezone are
-- windows.LEGAL_WINDOW / PREFERENCE_WINDOW (the calling window) and the draft's
-- "I called your office this morning" phrase. Neither applies.
--
-- ⚠️  THIS REMOVES A STRUCTURAL GUARANTEE, and that is the risk in this
--     migration. Until now the SCHEMA made a lead without a number impossible,
--     so it was impossible to dial one. From here only CODE does:
--
--       dialer.PHONE_REQUIRED     a phoneless lead is never a CANDIDATE
--       guards.assert_has_phone   and it is refused LOUDLY before any dial,
--                                 with a dial_audit row, rather than silently
--                                 skipped
--
--     Breaks 106 and 107. Both exist because "the column cannot be null" is a
--     stronger statement than "the query filters it out", and we just traded the
--     first for the second.
--
--     In unrestricted mode a NULL would otherwise reach
--     retell.create_phone_call(to_number=None) and surface as an API error,
--     which reads as a Retell problem rather than a bad lead.
ALTER TABLE leads ALTER COLUMN phone_e164 DROP NOT NULL;
ALTER TABLE leads ALTER COLUMN timezone   DROP NOT NULL;

-- The uniqueness of a real number still matters - two leads for one firm is how
-- a firm gets called twice. But NULLs must not collide with each other, and in
-- Postgres a plain UNIQUE already permits many NULLs; making it PARTIAL says so
-- explicitly and keeps the index off the imported rows entirely.
DROP INDEX IF EXISTS leads_phone_uniq;
CREATE UNIQUE INDEX leads_phone_uniq ON leads (phone_e164)
  WHERE phone_e164 IS NOT NULL;

-- The format CHECK already tolerates NULL (a CHECK passes on NULL), so a bad
-- number is still refused and an absent one is allowed. Left as it is.

COMMENT ON COLUMN leads.phone_e164 IS
  'E.164, or NULL for an email-only lead. NULL is NOT a dialable state: dialer.PHONE_REQUIRED excludes it from selection and guards.assert_has_phone refuses it loudly before any dial. Add a number by hand and the lead becomes dialable - a timezone is required in the same save, because a NULL timezone matches no calling window and would leave the lead looking dialable while permanently filtered out.';

COMMENT ON COLUMN leads.timezone IS
  'IANA name, or NULL for an email-only lead. Read ONLY by the calling window and the draft''s time-of-day phrase. A lead with a phone MUST have one - see api/web.py contact save.';

-- ============================================================================
-- 2. WHERE THE LEAD CAME FROM, AND WHY IT IS NOT dm_email_confirmed
-- ============================================================================
--
-- autosend.eligibility() holds a lead whose email was not CONFIRMED BY THE AGENT
-- and one with NO CONTACT NAME. Both exist as a substitute for a human having
-- verified the contact: for a call-sourced lead the evidence is a spellback on a
-- recorded call.
--
-- An imported lead has different evidence - a person chose to upload the file -
-- asserted ONCE for a whole batch rather than per lead. That is a legitimate
-- basis and a DIFFERENT one.
--
-- So it is recorded rather than faked. Setting dm_email_confirmed = true on
-- import would make that flag mean two different things depending on where the
-- lead came from, and the gate could not tell them apart: one fact, two homes,
-- which is the fault this repo keeps paying for. The exclusions become
-- SOURCE-AWARE instead, and the call path is left byte-for-byte as strict as it
-- was - asserted by a test, not claimed here.
ALTER TABLE leads
  ADD COLUMN lead_source text NOT NULL DEFAULT 'call'
    CONSTRAINT leads_source_check CHECK (lead_source IN ('call', 'import'));

CREATE INDEX leads_source ON leads (lead_source);

COMMENT ON COLUMN leads.lead_source IS
  'call = we dialled it. import = it arrived as a CSV of company + email. Provenance, deliberately NOT the name of whoever supplied the list - that is a note on the upload, not a schema decision. Read by autosend.eligibility() (source-aware exclusions) and available as a funnel filter, so call-sourced and imported can be compared. It NEVER changes: a lead that arrived by import and later gets a phone by hand stays ''import'', because the record is where it came from, not what has happened to it since.';

-- ============================================================================
-- 3. WHICH SEQUENCE FOLLOWS A CALL CAMPAIGN'S EMAIL 1
-- ============================================================================
--
-- ⚠️  WITHOUT THIS, STANDING UP A SECOND DRIP SILENTLY STOPS THE FIRST ONE'S
--     FOLLOW-UPS. drip.only_drip() auto-assigns only when EXACTLY ONE drip is
--     running, so the moment there are two - a call-sourced sequence and an
--     imported one - every call-sourced lead that gets email 1 joins NO DRIP AT
--     ALL. Email 1 goes out, the lead sits at 'emailed', and nothing follows up
--     until somebody assigns it by hand, one firm at a time.
--
--     A campaign already owns email 1's COPY, so owning what FOLLOWS email 1 is
--     the same shape rather than a new concept. only_drip() is demoted to a
--     fallback for the single-drip case; this is the mechanism.
--
--     The safety net for whatever this does not cover is /today's needs-you
--     queue, which now surfaces "emailed and on no drip" - because a stalled
--     lead that is invisible until somebody opens it is the failure this whole
--     change is about.
ALTER TABLE campaign_configs
  ADD COLUMN default_drip_id uuid REFERENCES campaign_configs(campaign_id);

COMMENT ON COLUMN campaign_configs.default_drip_id IS
  'CALL campaigns: which DRIP campaign this campaign''s leads enter when email 1 is sent. NULL falls back to drip.only_drip(), which assigns only when exactly one drip runs. Set this before standing up a second drip, or call-sourced follow-ups stop silently.';
