-- THE SCORER MUST JUDGE AGAINST THE PROMPT THE AGENT WAS ACTUALLY GIVEN.
--
-- It scored against a generic notion of good conduct, so a prompt with named
-- rules - Anti-Loop, One-Redirect, the two-turn email gate, answer-and-stop -
-- was invisible to it. "It repeated itself" and "it broke the Anti-Loop Rule"
-- are different findings: one suggests rewriting the prompt, the other says
-- the model is ignoring it, and they have different fixes.
ALTER TABLE call_scores ADD COLUMN rules_violated text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN call_scores.rules_violated IS
  'Named rules from the AGENT PROMPT this call ran, that the agent broke. '
  'Names come from the prompt itself, never a hardcoded list - the prompt is '
  'operator-edited and a fixed list would drift away from it silently.';

-- prompt_version was stamped with (SELECT version FROM prompt_versions ORDER BY
-- version DESC LIMIT 1) - the surrogate PK of the NEWEST row for the stage, not
-- the agent version that dialed. Three existing scores said 17 for calls that
-- ran v15, so every score was attributed to a prompt that did not place it.
COMMENT ON COLUMN call_scores.prompt_version IS
  'The Retell AGENT VERSION that placed this call (calls.agent_version). '
  'Was previously a prompt_versions surrogate key, which attributed every '
  'score to the wrong prompt.';
