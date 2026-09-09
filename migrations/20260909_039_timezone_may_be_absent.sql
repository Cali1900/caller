-- A NULL TIMEZONE IS NOW LEGITIMATE, AND A WRONG ONE STILL IS NOT.
--
-- assert_iana_timezone() (migration 003) rejected NULL along with everything
-- else, which was right while every lead came from a call list: leads.timezone
-- was NOT NULL, so a NULL could only mean a bug.
--
-- Migration 038 made it nullable for email-only leads, which have nothing to
-- derive a timezone from and no use for one - the ONLY consumers are the calling
-- window and the draft's time-of-day phrase.
--
-- ⚠️  THE GUARD'S REAL JOB IS UNCHANGED, and it must not be weakened by this.
--     Postgres accepts '-05:00' in AT TIME ZONE without complaint, so nothing
--     caught a hand-written offset before this trigger existed - and that
--     failure is SILENT for a few weeks after each DST change, which is the
--     worst kind. A non-NULL value must still be a real IANA region/city name.
--
--     So: ABSENT is allowed, WRONG is still refused. Those are different
--     things, and collapsing them is what made the old check reject the
--     legitimate case.
CREATE OR REPLACE FUNCTION assert_iana_timezone() RETURNS trigger AS $$
BEGIN
    -- Absent is fine: an email-only lead has no timezone and needs none.
    IF NEW.timezone IS NULL THEN
        RETURN NEW;
    END IF;
    IF position('/' in NEW.timezone) = 0
       OR NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = NEW.timezone)
    THEN
        RAISE EXCEPTION
          'leads.timezone must be an IANA region/city name (e.g. America/New_York), got %',
          NEW.timezone
          USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION assert_iana_timezone() IS
  'Refuses a timezone that is not an IANA region/city name. NULL is permitted since migration 038 (email-only leads); an offset like -05:00 is still refused, because Postgres accepts it in AT TIME ZONE and the resulting error is silent for weeks after each DST change.';
