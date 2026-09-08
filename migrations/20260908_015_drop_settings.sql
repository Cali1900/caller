-- DELETE THE SETTINGS TABLE. Nothing reads it.
--
-- Every key moved onto campaign_configs when campaigns became named
-- configurations. What was left was ten inert rows that still LOOKED
-- authoritative - and that is not a cosmetic problem:
--
--   scripts/deploy.sh paused before every restart by writing
--   settings['dialing_enabled'], which nothing had consulted for days. Its
--   "pause" was a no-op, so a deploy during calling hours would have rebuilt
--   straight through a live call while reporting that it had paused.
--
-- Contents archived to:
--   /root/caller-archive/settings_table_20260908.sql
--   /root/caller-archive/settings.py.deleted-20260908
--
-- Migrations 007-011 still reference this table and still run in order on a
-- fresh database: 007 creates it, 011 reads it to seed the first campaign,
-- and this drops it afterwards. History is preserved, not rewritten.

-- The version range used to be enforced by settings.set_many. Enforce it where
-- the value now lives, so a negative or absurd version cannot be written by
-- anything - a script or a stray UPDATE included, which the Python check never
-- covered.
ALTER TABLE campaign_configs
  ADD CONSTRAINT campaign_configs_agent_l1_version_check
      CHECK (agent_l1_version >= 0 AND agent_l1_version <= 9999),
  ADD CONSTRAINT campaign_configs_agent_l3_version_check
      CHECK (agent_l3_version >= 0 AND agent_l3_version <= 9999);

DROP TABLE IF EXISTS settings;
