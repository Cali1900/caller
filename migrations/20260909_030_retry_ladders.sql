-- RETRY GAPS PER CAMPAIGN, AS LADDERS.
--
-- BACKOFF was a flat dict in drain.py: busy 15m and no_answer 2h regardless
-- of attempt. At four attempts that is four calls to the same firm inside
-- eight hours, which is a pattern a receptionist notices and the opposite of
-- what the spacing work was for.
--
-- A rung is either a DURATION ('15m', '4h', '3d') or the literal 'next_day',
-- which means 09:00 tomorrow in the CALLED PARTY's timezone. Both are needed:
-- "4 hours" cannot express "tomorrow morning their time", and a fixed 24h
-- lands at whatever hour the last attempt happened to fall on.
--
-- Both shapes bind their values as parameters. The old voicemail rule was the
-- one place BACKOFF interpolated SQL, because a bound interval cannot
-- reference l.timezone; naming the two shapes separately means the timezone
-- expression is a fixed fragment and the TIME inside it is bound.
ALTER TABLE campaign_configs
  ADD COLUMN retry_busy      text[] NOT NULL DEFAULT '{15m,1h,4h,next_day}',
  ADD COLUMN retry_no_answer text[] NOT NULL DEFAULT '{2h,8h,1d,3d}',
  ADD COLUMN retry_voicemail text[] NOT NULL DEFAULT '{next_day}',
  -- MAX ATTEMPTS BELONGS NEXT TO THE LADDERS. It was hardcoded at 4 in
  -- drain.py, and a rung only fires if an attempt follows it - so at 4 the
  -- fourth rung of a four-rung ladder could never fire. Editing a value that
  -- does nothing is the dead-surface fault in its most expensive form: it
  -- reads as a setting and it is a decoration.
  --
  -- Default stays 4, so dialing volume does not change on this migration.
  ADD COLUMN max_attempts    int NOT NULL DEFAULT 4
    CONSTRAINT campaign_configs_max_attempts_check
    CHECK (max_attempts BETWEEN 1 AND 10);

COMMENT ON COLUMN campaign_configs.retry_busy IS
  'Ladder of waits after a busy signal, one rung per attempt. Each rung is a duration (15m/4h/3d) or next_day (09:00 tomorrow in the LEAD''s timezone). Busy is deliberately shortest - it means a human is there, the best signal in the list. Rung N only fires if attempt N+1 is allowed by max_attempts.';
