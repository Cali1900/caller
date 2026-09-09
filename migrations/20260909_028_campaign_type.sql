-- CAMPAIGN TYPE: call or drip.
--
-- Groundwork. No drip campaign exists yet and none of this changes what a
-- call campaign does; it is here now because it is one line today and a
-- migration against live campaigns with leads attached later.
--
-- A call campaign owns a prompt, a cap, spacing and calling windows, and
-- exactly ONE runs at a time - there is one phone number and one worker, and
-- the stop-and-swap refusal in campaigns.start() is deliberate.
--
-- A drip campaign owns email copy, intervals and a sender, and MANY run at
-- once: a lead's sequence is a property of the lead, not of whichever call
-- campaign happened to source it.
--
-- one_running_campaign was UNIQUE (is_running) WHERE is_running, which would
-- have refused the second drip - and refused it from inside Postgres, where
-- the error names an index and not a reason. Scoped to call campaigns.
ALTER TABLE campaign_configs
  ADD COLUMN type text NOT NULL DEFAULT 'call'
  CONSTRAINT campaign_configs_type_check CHECK (type IN ('call', 'drip'));

DROP INDEX IF EXISTS one_running_campaign;
CREATE UNIQUE INDEX one_running_campaign ON campaign_configs ((is_running))
  WHERE is_running AND type = 'call';

COMMENT ON COLUMN campaign_configs.type IS
  'call = dials, one at a time (one_running_campaign). drip = emails, many at a time. Set at creation; changing it under live leads is not a supported edit, so it is absent from CONFIG_FIELDS and update() refuses it.';
