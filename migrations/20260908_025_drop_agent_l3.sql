-- agent_l3_version IS DEAD CONFIG. Same shape as the settings keys that made
-- deploy.sh report a pause it was not performing.
--
-- L3 calling was descoped: a follow-up is email, and STAGE_DIALABLE is L1 only.
-- Nothing that dials reads this column. It was still displayed on two screens,
-- which is worse than being unused - a number on a screen reads as a number
-- that matters.
--
-- The L3 AGENT still exists in Retell, untouched. This removes the app's
-- pointer at it, not the agent.
ALTER TABLE campaign_configs DROP CONSTRAINT IF EXISTS campaign_configs_agent_l3_version_check;
ALTER TABLE campaign_configs DROP COLUMN agent_l3_version;
