-- Prompt version selection from the UI.
--
-- v8 became live as a SIDE EFFECT: agent.update() applies to whatever draft is
-- sitting there, so editing in the dashboard silently moved the live prompt.
-- The live version is now an explicit operator choice stored here, and the
-- dialer reads it - editing a draft in Retell no longer changes what dials.

ALTER TABLE calls ADD COLUMN agent_version int;

COMMENT ON COLUMN calls.agent_version IS
  'The Retell agent version this call actually ran, stamped from call metadata. Exact attribution: a score shift can always be tied to a prompt.';

CREATE INDEX calls_agent_version ON calls (agent_version, created_at DESC);

-- prompt_versions gains the fields the picker shows.
ALTER TABLE prompt_versions ADD COLUMN retell_updated_at timestamptz;
ALTER TABLE prompt_versions ADD COLUMN is_published boolean;

-- The live version per stage. Seeded from the versions that carry signed
-- recording URLs (L1 v9, L3 v1); the prompts are byte-identical to v8/v0.
INSERT INTO settings (key, value, updated_by) VALUES
    ('agent_l1_version', '9', 'seed'),
    ('agent_l3_version', '1', 'seed')
ON CONFLICT (key) DO NOTHING;
