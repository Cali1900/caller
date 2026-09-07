-- Phase 2: campaigns, windows, cap, rollover.
-- Forward-only. Never edit an applied migration.

-- ===========================================================================
-- prompt_versions: record WHAT RAN, not just the text.
--
-- Measured 2026-09-07 across four real calls: swapping the model alone
-- (claude-5-sonnet -> gpt-4.1) on a byte-identical 8,047-char prompt moved
-- llm p50 from 2836ms to 508ms - an 82% drop. A score change after a swap
-- like that is unattributable if the row only stores prompt text.
--
-- Prompt length matters too, and SUPERLINEARLY: within one model,
-- 0.082 ms/char between 3.4k and 5.3k chars, but 0.170 ms/char at 8k.
-- Storing prompt_chars lets that curve be re-derived later instead of
-- re-measured.
-- ===========================================================================
ALTER TABLE prompt_versions ADD COLUMN model         text;
ALTER TABLE prompt_versions ADD COLUMN prompt_chars  int;
ALTER TABLE prompt_versions ADD COLUMN agent_version int;

COMMENT ON COLUMN prompt_versions.model IS
  'In-call LLM. The dominant latency term: 82% swing on an identical prompt.';
COMMENT ON COLUMN prompt_versions.prompt_chars IS
  'Prompt size. Latency cost per char is superlinear: ~0.082 ms/char at 3.4k, ~0.170 ms/char at 8k.';
COMMENT ON COLUMN prompt_versions.agent_version IS
  'Retell agent version actually dialed (we pin override_agent_version).';

CREATE UNIQUE INDEX prompt_versions_agent_ver
    ON prompt_versions (agent_id, agent_version)
    WHERE agent_version IS NOT NULL;

-- ===========================================================================
-- Campaign selection support.
--
-- The dialer's hot path joins leads -> campaign_leads for today and filters
-- on the calling window. This index covers the undialed lookup per day.
-- ===========================================================================
CREATE INDEX campaign_leads_lead ON campaign_leads (lead_id);

-- Rollover: a lead that has been due-but-undialed for days on end is a
-- signal, not noise. Flagged at 5 days (see api/campaigns.py).
CREATE INDEX leads_rollover ON leads (rollover_days)
    WHERE rollover_days >= 5;

-- ===========================================================================
-- last_dispositions: the retry ladder is per-reason from phase 2 on
-- (busy 15m, no answer 2h, voicemail next day), so the drain needs to know
-- WHY the last attempt failed. Nothing recorded that before.
-- ===========================================================================
ALTER TABLE leads ADD COLUMN last_outcome text;

COMMENT ON COLUMN leads.last_outcome IS
  'Why the last attempt ended. Drives the per-reason backoff: busy 15m, no_answer 2h, voicemail next day.';
