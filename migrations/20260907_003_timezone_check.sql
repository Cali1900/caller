-- Phase 2 (second migration - 002 was already applied, and an applied
-- migration is never edited).
--
-- leads.timezone must be an IANA REGION/CITY name.
--
-- Nothing enforced this before. Postgres accepts '-05:00' in AT TIME ZONE
-- without complaint, so a hand-written INSERT - which is exactly how leads
-- went in during phase 1 - could store a fixed offset. That is wrong twice a
-- year, and the failure is silent: the window query keeps returning rows, just
-- the wrong ones, for a few weeks after each DST change.
--
-- Validated against pg_timezone_names (authoritative, so typos like
-- 'America/New_york' are caught too) AND required to contain a '/', which
-- rejects fixed offsets, bare abbreviations like 'EST', and 'UTC'.

CREATE OR REPLACE FUNCTION assert_iana_timezone() RETURNS trigger AS $$
BEGIN
    IF NEW.timezone IS NULL
       OR position('/' in NEW.timezone) = 0
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

CREATE TRIGGER leads_timezone_is_iana
    BEFORE INSERT OR UPDATE OF timezone ON leads
    FOR EACH ROW EXECUTE FUNCTION assert_iana_timezone();
