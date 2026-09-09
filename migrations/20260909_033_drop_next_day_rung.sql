-- DROP THE 'next_day' RUNG. A rung is a duration and nothing else.
--
-- THE CALLING WINDOW IS THE CLAMP, NOT THE LADDER. next_attempt_at is a
-- NOT-BEFORE gate, never a scheduled dial time: windows.LEGAL_WINDOW and
-- windows.PREFERENCE_WINDOW are ANDed into the selection query and evaluated
-- in the called party's local time, so no rung value can cause a call outside
-- the allowed hours. A gap landing at 03:00 just waits for the window to open.
--
-- 'next_day' computed 09:00 tomorrow in the lead's timezone. The argument for
-- it was that a plain '1d' after a 19:50 dial lands at 19:50, which the window
-- then pushes to the following morning - a day later than intended. That is
-- only reachable when the preference window is wide enough to have dialed at
-- 19:50 in the first place. Under the default 09:00-17:00 it cannot happen:
-- the previous attempt was inside the window, so the same local time tomorrow
-- is inside it too.
--
-- So it bought nothing at the configured hours, and it cost the only raw-SQL
-- fragment in api/retry_ladder.py, a special case in three functions, and its
-- own break definition (84, deleted with this change). If the evening window
-- is ever widened, reconsider it - and bring it back with a test that widens
-- the window, because that is the only condition under which it is observable.
--
-- 'next_day' -> '1d'. Same rung position, same escalation shape; the hour it
-- lands on is the calling window's business, which is where it belonged.

ALTER TABLE campaign_configs
  ALTER COLUMN retry_busy      SET DEFAULT '{15m,1h,4h,1d}',
  ALTER COLUMN retry_voicemail SET DEFAULT '{1d}';

-- Existing campaigns. C1 is live and carries next_day in retry_busy and
-- retry_voicemail; leaving them would make every ladder unreadable to
-- parse(), and drain._retry would fall back on every single retry.
UPDATE campaign_configs
   SET retry_busy      = array_replace(retry_busy,      'next_day', '1d'),
       retry_no_answer = array_replace(retry_no_answer, 'next_day', '1d'),
       retry_voicemail = array_replace(retry_voicemail, 'next_day', '1d')
 WHERE 'next_day' = ANY(retry_busy)
    OR 'next_day' = ANY(retry_no_answer)
    OR 'next_day' = ANY(retry_voicemail);

COMMENT ON COLUMN campaign_configs.retry_busy IS
  'Ladder of waits after a busy signal, one rung per attempt. Each rung is a duration (15m/4h/3d) - the calling window clamps the hours, so the ladder never does. Busy is deliberately shortest: it means a human is there, the best signal in the list. Rung N only fires if attempt N+1 is allowed by max_attempts.';

COMMENT ON COLUMN campaign_configs.retry_voicemail IS
  'Ladder of waits after voicemail, one rung per attempt. Each rung is a duration (15m/4h/3d).';
