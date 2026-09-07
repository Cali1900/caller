-- Phase 5b: operator-editable settings.
--
-- Spacing and windows must change WITHOUT a deploy. Env vars require a
-- container recreate, so env is only the first-boot seed - the live value
-- lives here and the CRM edits it.
--
-- Sender domain deliberately stays in env: switching the sending domain is a
-- rare, deliberate act that should carry a restart, not a web form.

CREATE TABLE settings (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text
);

COMMENT ON TABLE settings IS
  'Operator-editable runtime settings. DB value WINS over the env seed. Edited from /campaign.';

-- Seeded from the operator's stated numbers, not from a guess:
--   MAX_CONCURRENT 1   - one call in flight at a time
--   210-300s jitter    - ~4 min average. A FIXED cadence is itself a pattern.
--   daily cap 100      - first month
INSERT INTO settings (key, value, updated_by) VALUES
    ('max_concurrent',    '1',   'seed'),
    ('dial_interval_min', '210', 'seed'),
    ('dial_interval_max', '300', 'seed')
ON CONFLICT (key) DO NOTHING;

-- First month is 100/day.
UPDATE campaigns SET daily_cap = 100 WHERE daily_cap = 200;
