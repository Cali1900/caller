-- A CAMPAIGN IS A NAMED CONFIGURATION.
--
-- Not a day, and not a prompt version. It OWNS the prompt version, the sender
-- identity, the cap, the spacing, the calling windows and its notes. Two
-- campaigns can both run v9 - the version is a PROPERTY of a campaign, never
-- the thing that identifies it.
--
-- Leads are ASSIGNED to a campaign. Assignment and queued-ness are separate:
-- switching which campaign runs reassigns nothing, so a campaign resumes
-- exactly where it left off.

CREATE TABLE campaign_configs (
    campaign_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL UNIQUE,
    notes               text,

    -- the prompt version is a property of the campaign
    agent_l1_version    int NOT NULL,
    agent_l3_version    int NOT NULL,

    sender_email        text NOT NULL,
    sender_name         text NOT NULL,
    sender_company_line text NOT NULL,

    daily_cap           int NOT NULL DEFAULT 100  CHECK (daily_cap BETWEEN 1 AND 5000),
    max_concurrent      int NOT NULL DEFAULT 1    CHECK (max_concurrent BETWEEN 1 AND 10),
    dial_interval_min   int NOT NULL DEFAULT 210  CHECK (dial_interval_min BETWEEN 15 AND 3600),
    dial_interval_max   int NOT NULL DEFAULT 300  CHECK (dial_interval_max BETWEEN 15 AND 7200),
    CHECK (dial_interval_min <= dial_interval_max),

    -- Running replaces the old global dialing_enabled switch. Defaults false:
    -- creating a campaign, or assigning leads to one, can never start dialing.
    is_running          boolean NOT NULL DEFAULT false,
    started_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- ⚠️ ONE RUNNING AT A TIME, ENFORCED BY THE DATABASE.
-- Not by application convention, and not by the UI being careful. A second
-- concurrent start is rejected by Postgres, so the "never two running" rule
-- survives a bug, a race and a direct SQL edit.
CREATE UNIQUE INDEX one_running_campaign ON campaign_configs ((is_running))
    WHERE is_running;

COMMENT ON COLUMN campaign_configs.is_running IS
  'Exactly one campaign may be true at a time - enforced by one_running_campaign. Starting another requires explicitly stopping this one; the system never swaps silently.';

-- Windows are PER CAMPAIGN. A new campaign is seeded Mon-Fri 09:00-17:00:
-- an empty week is a campaign that silently dials nothing, which is worse
-- than a default that has to be changed.
CREATE TABLE campaign_windows (
    campaign_id uuid NOT NULL REFERENCES campaign_configs(campaign_id) ON DELETE CASCADE,
    dow         int NOT NULL CHECK (dow BETWEEN 0 AND 6),
    enabled     boolean NOT NULL DEFAULT true,
    start_time  time NOT NULL DEFAULT '09:00',
    end_time    time NOT NULL DEFAULT '17:00',
    PRIMARY KEY (campaign_id, dow)
);

-- Assignment. Separate from pool_status, which is queued-ness.
ALTER TABLE leads ADD COLUMN campaign_id uuid
    REFERENCES campaign_configs(campaign_id) ON DELETE SET NULL;

CREATE INDEX leads_campaign ON leads (campaign_id)
    WHERE campaign_id IS NOT NULL;

COMMENT ON COLUMN leads.campaign_id IS
  'Which campaign this lead belongs to. Leads on a campaign that is not running sit idle - they do not dial. Switching campaigns reassigns nothing.';

-- ===========================================================================
-- Seed C1 from whatever is configured today, so nothing is lost, and adopt
-- the leads that are already queued.
-- ===========================================================================
INSERT INTO campaign_configs (
    name, notes, agent_l1_version, agent_l3_version,
    sender_email, sender_name, sender_company_line,
    daily_cap, max_concurrent, dial_interval_min, dial_interval_max, is_running)
SELECT
    'C1',
    'Seeded from the global settings that existed before campaigns were named.',
    COALESCE((SELECT value::int FROM settings WHERE key='agent_l1_version'), 9),
    COALESCE((SELECT value::int FROM settings WHERE key='agent_l3_version'), 1),
    COALESCE((SELECT value      FROM settings WHERE key='sender_email'), 'sean@counselorai.io'),
    COALESCE((SELECT value      FROM settings WHERE key='sender_name'), 'Sean'),
    COALESCE((SELECT value      FROM settings WHERE key='sender_company_line'), 'CounselorAI LLC'),
    COALESCE((SELECT value::int FROM settings WHERE key='daily_cap'), 100),
    COALESCE((SELECT value::int FROM settings WHERE key='max_concurrent'), 1),
    COALESCE((SELECT value::int FROM settings WHERE key='dial_interval_min'), 210),
    COALESCE((SELECT value::int FROM settings WHERE key='dial_interval_max'), 300),
    false;   -- seeded STOPPED. Starting is always a deliberate act.

-- carry the existing global windows onto C1
INSERT INTO campaign_windows (campaign_id, dow, enabled, start_time, end_time)
SELECT c.campaign_id, w.dow, w.enabled, w.start_time, w.end_time
  FROM campaign_configs c, dialing_windows w
 WHERE c.name = 'C1';

-- every existing lead belongs to C1
UPDATE leads SET campaign_id = (SELECT campaign_id FROM campaign_configs WHERE name='C1')
 WHERE campaign_id IS NULL;

-- The old per-day campaigns/campaign_leads tables are superseded. Left in
-- place (migrations are forward-only) but nothing reads them any more.
COMMENT ON TABLE campaigns IS
  'SUPERSEDED by campaign_configs. Was one row per day; campaigns are now named configurations. Nothing reads this.';
