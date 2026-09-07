-- Caller: initial schema.
-- Forward-only. Never edit an applied migration.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ===========================================================================
-- suppression — the DNC list.
-- Joined into the SELECT, never checked at dial time. Highest-liability
-- object in the system: $500-$1,500 per violation, no cap. Back it up.
-- ===========================================================================
CREATE TABLE suppression (
    phone_e164  text PRIMARY KEY,
    reason      text NOT NULL,   -- requested | federal_dnc | state_dnc | manual
    source      text,            -- call_id, import file, operator
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ===========================================================================
-- leads
--
-- pool_status  where the lead sits: uploaded but idle, or in play.
--              Uploading NEVER dials. A lead must be added to a campaign.
-- stage        how far through the ladder: L1 -> L2 -> L3 -> L4
-- status       what is happening right now
-- ===========================================================================
CREATE TABLE leads (
    lead_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    company          text NOT NULL,
    phone_e164       text NOT NULL,
    timezone         text NOT NULL,   -- IANA. NOT an offset: an offset breaks at DST.
    city             text,
    state            text,
    segment          text NOT NULL DEFAULT 'default',
    external_ref     text,

    pool_status      text NOT NULL DEFAULT 'pool',   -- pool | active | done
    stage            text NOT NULL DEFAULT 'L1',
    status           text NOT NULL DEFAULT 'new',

    attempts         int  NOT NULL DEFAULT 0,
    stage_attempts   int  NOT NULL DEFAULT 0,
    last_called_at   timestamptz,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    last_call_id     text,
    rollover_days    int  NOT NULL DEFAULT 0,   -- consecutive days due-but-not-dialed

    -- what we are calling to get
    dm_name          text,
    dm_title         text,
    dm_email         text,
    dm_email_confirmed boolean,
    dm_email_source  text,          -- transcript segment, for adjudication

    -- callback handling. Resolved to a timestamp by the worker.
    -- Nobody can act on the string 'next Tuesday'.
    callback_person  text,
    callback_count   int NOT NULL DEFAULT 0,
    callback_vague   boolean,

    -- L2: you email them
    emailed_at       timestamptz,
    emailed_by       text,

    -- L4 outcome
    demo_agreed_at   timestamptz,
    demo_when        timestamptz,
    demo_when_tz     text,
    demo_email       text,
    invite_sent_at   timestamptz,

    notes            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT leads_pool_status_check CHECK (pool_status IN ('pool','active','done')),
    CONSTRAINT leads_stage_check CHECK (stage IN ('L1','L2','L3','L4','won','lost')),
    CONSTRAINT leads_status_check CHECK (status IN (
        'new','queued','dialing','completed','callback','no_answer',
        'email_path',      -- "send us an email" is a LEAD, not a failure.
                           -- leaves the dialer entirely.
        'demo_pending',    -- verbal yes. YOU owe an invite.
        'dnc','max_attempts','failed','human_review','paused'
    )),
    CONSTRAINT leads_phone_check CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$')
);

CREATE UNIQUE INDEX leads_phone_uniq ON leads (phone_e164);
CREATE INDEX leads_due ON leads (next_attempt_at)
    WHERE pool_status = 'active'
      AND status IN ('new','callback','no_answer','queued');
CREATE INDEX leads_pool ON leads (pool_status, state, created_at);
CREATE INDEX leads_stage ON leads (stage, status);
CREATE INDEX leads_needs_you ON leads (updated_at)
    WHERE status IN ('demo_pending','human_review');

COMMENT ON COLUMN leads.timezone IS
  'IANA zone. The 8:00-20:30 window is evaluated in the CALLED PARTY local time.';

-- ===========================================================================
-- dialing_windows — your preference, per weekday, in CLIENT local time.
-- Can only ever NARROW the legal window, never widen it.
-- ===========================================================================
CREATE TABLE dialing_windows (
    dow        int PRIMARY KEY CHECK (dow BETWEEN 0 AND 6),  -- 0=Sunday
    enabled    boolean NOT NULL DEFAULT true,
    start_time time NOT NULL DEFAULT '09:00',
    end_time   time NOT NULL DEFAULT '17:00'
);

-- Mon-Fri on, weekend off. Adjust in the UI.
INSERT INTO dialing_windows (dow, enabled, start_time, end_time) VALUES
    (0, false, '09:00', '17:00'),
    (1, true,  '09:00', '17:00'),
    (2, true,  '09:00', '17:00'),
    (3, true,  '09:00', '17:00'),
    (4, true,  '09:00', '17:00'),
    (5, true,  '09:00', '17:00'),
    (6, false, '09:00', '17:00');

-- ===========================================================================
-- campaigns — one per day. Nothing dials until started_at is set.
-- ===========================================================================
CREATE TABLE campaigns (
    campaign_date  date PRIMARY KEY,
    daily_cap      int NOT NULL DEFAULT 200,
    started_at     timestamptz,
    paused         boolean NOT NULL DEFAULT false,
    dialed_count   int NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE campaign_leads (
    campaign_date date NOT NULL REFERENCES campaigns(campaign_date) ON DELETE CASCADE,
    lead_id       uuid NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    source        text NOT NULL,   -- fresh | callback | followup | retry | rollover
    added_at      timestamptz NOT NULL DEFAULT now(),
    dialed_at     timestamptz,
    PRIMARY KEY (campaign_date, lead_id)
);

CREATE INDEX campaign_leads_undialed ON campaign_leads (campaign_date)
    WHERE dialed_at IS NULL;

-- ===========================================================================
-- calls — append-only. One row per attempt. Never overwrite.
-- ===========================================================================
CREATE TABLE calls (
    call_id              text PRIMARY KEY,
    lead_id              uuid NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    stage                text NOT NULL,
    agent_id             text,
    prompt_version       int,

    started_at           timestamptz,
    ended_at             timestamptz,
    duration_ms          int,
    disconnection_reason text,
    call_status          text,

    transcript           text,
    recording_url        text,
    analysis             jsonb,   -- Retell call_analysis
    latency              jsonb,   -- log from day one: tells you whether P50 is
                                  -- your prompt or the platform floor
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX calls_lead ON calls (lead_id, created_at DESC);
CREATE INDEX calls_created ON calls (created_at DESC);

-- ===========================================================================
-- prompt_versions — every script change versioned, every call records which
-- ran. Without this you can see a score move but not what caused it.
-- ===========================================================================
CREATE TABLE prompt_versions (
    version      serial PRIMARY KEY,
    stage        text NOT NULL,
    agent_id     text NOT NULL,
    prompt_text  text NOT NULL,
    changed_by   text NOT NULL,
    change_note  text NOT NULL,
    active_from  timestamptz NOT NULL DEFAULT now(),
    active_to    timestamptz
);

-- ===========================================================================
-- call_scores — one LLM pass per call. EVERY call, not a sample.
--
-- TWO scores on purpose:
--   agent_score    what WE control. drive this to 10/10.
--   outcome_score  the business metric. will never be 10 across the board.
--
-- A high agent_score with a low outcome_score means the target list or the
-- ask is wrong, not the script. One combined number destroys that signal.
-- ===========================================================================
CREATE TABLE call_scores (
    call_id          text PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    lead_id          uuid REFERENCES leads(lead_id) ON DELETE CASCADE,
    stage            text NOT NULL,
    prompt_version   int REFERENCES prompt_versions(version),

    outcome_score    int NOT NULL CHECK (outcome_score BETWEEN 0 AND 10),
    agent_score      int NOT NULL CHECK (agent_score BETWEEN 0 AND 10),
    agent_deductions text[],   -- rambled | no_email_ask | no_spellback |
                               -- talked_over | sounded_salesy | no_disclosure

    what_happened    text NOT NULL,
    where_it_broke   text,
    their_words      text,     -- verbatim. the percentage says THAT.
                               -- this says WHAT, which is what you can
                               -- write a better opener against.
    we_got           text[],
    next_move        text,
    needs_human      boolean NOT NULL DEFAULT false,

    model            text,
    scored_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT what_happened_check CHECK (what_happened IN (
        'not_interested','send_email','busy_callback','gave_name',
        'gave_name_and_email','gatekeeper_block','voicemail','busy',
        'no_answer','ivr_trapped','wrong_number','remove_me',
        'demo_agreed','demo_declined','transferred','other'
    ))
);

CREATE INDEX call_scores_what ON call_scores (what_happened, scored_at DESC);
CREATE INDEX call_scores_stage ON call_scores (stage, scored_at DESC);
CREATE INDEX call_scores_human ON call_scores (scored_at) WHERE needs_human;

-- ===========================================================================
-- webhook_events — durable inbox.
-- The HTTP handler does ONE insert and returns 200. Retell times out at 10s
-- and retries 3x on non-2xx, so the handler must not do real work.
-- Idempotency is the (call_id, event) primary key.
-- ===========================================================================
CREATE TABLE webhook_events (
    call_id      text NOT NULL,
    event        text NOT NULL,
    payload      jsonb NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    attempts     int NOT NULL DEFAULT 0,
    last_error   text,
    PRIMARY KEY (call_id, event)
);

CREATE INDEX webhook_unprocessed ON webhook_events (received_at)
    WHERE processed_at IS NULL;

-- ===========================================================================
-- dial_audit — why a number was or was not dialed.
-- A silent refusal is how you spend an hour asking "why did nothing dial".
-- ===========================================================================
CREATE TABLE dial_audit (
    id          bigserial PRIMARY KEY,
    lead_id     uuid REFERENCES leads(lead_id) ON DELETE SET NULL,
    phone_e164  text NOT NULL,
    call_id     text,
    outcome     text NOT NULL,   -- dialed | refused_allowlist | refused_window
                                 -- | refused_suppressed | refused_cap | api_error
    detail      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX dial_audit_created ON dial_audit (created_at DESC);

-- ===========================================================================
-- activity — the CRM timeline. One row per thing that happened to a lead.
-- ===========================================================================
CREATE TABLE activity (
    id          bigserial PRIMARY KEY,
    lead_id     uuid NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    kind        text NOT NULL,   -- call | email_sent | note | stage_change
                                 -- | dnc | invite_sent | uploaded
    stage       text,
    call_id     text,
    summary     text NOT NULL,
    detail      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX activity_lead ON activity (lead_id, created_at DESC);
