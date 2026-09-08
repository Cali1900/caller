-- The named-campaign model superseded three tables and NOTHING READS THEM.
-- They are dropped rather than left in place because a dead table that still
-- looks authoritative is worse than no table: tests/test_windows.py was
-- asserting against `dialing_windows` and passing only because its seed
-- happened to match campaign_windows, so those window assertions were
-- checking a table with no effect on dialing.
--
-- Carried forward before dropping, verified 2026-09-08:
--   dialing_windows -> campaign_windows (all 7 rows identical for C1)
--   campaign_leads  -> leads.campaign_id + leads.pool_status='active'
--                      (its one row is assigned and queued)
--   campaigns       -> campaign_configs (the per-day campaign is gone; a
--                      campaign is a named configuration now)
-- Contents archived to
--   /root/caller-archive/pre_drop_legacy_campaign_tables_20260908.sql

DROP TABLE IF EXISTS campaign_leads;
DROP TABLE IF EXISTS campaigns;
DROP TABLE IF EXISTS dialing_windows;
