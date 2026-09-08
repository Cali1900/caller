"""
Call scoring. EVERY call, not a sample.

200 calls/day is far too much audio for a person to review, so the LLM does
the listening and a human reads one summary a day.

TWO SCORES, NEVER COMBINED. This is the whole point of the module:

  agent_score    what WE control. Starts at 10 and is deducted from. This
                 should sit at 9-10 once the script is right.
  outcome_score  the business metric. It will never be 10 across the board -
                 some firms simply will not tell you.

A HIGH agent_score with a LOW outcome_score means the target list or the ask
is wrong, NOT the script. Averaging them into one number destroys exactly that
signal, which is why there are two columns and no combined field anywhere.

their_words is the load-bearing field. The percentage tells you THAT calls
fail; the verbatim tells you WHAT was said, which is the only thing you can
write a better opener against.

THE LLM REPORTS. IT DOES NOT EDIT THE PROMPT. Nothing in this module writes to
prompt_versions or to any agent config. A person approves every script change.
"""

import json

import anthropic

from api import db

# Must match the what_happened_check CHECK constraint on call_scores.
WHAT_HAPPENED = [
    'not_interested', 'send_email', 'busy_callback', 'gave_name',
    'gave_name_and_email', 'gatekeeper_block', 'voicemail', 'busy',
    'no_answer', 'ivr_trapped', 'wrong_number', 'remove_me',
    'demo_agreed', 'demo_declined', 'transferred', 'other',
]
DEDUCTIONS = ['rambled', 'no_email_ask', 'no_spellback', 'talked_over',
              'sounded_salesy', 'no_disclosure', 'repeated_the_same_ask',
              # L3 only
              'reasked_known_info', 'no_name_in_opener', 'pitched']
NEXT_MOVE = ['retry', 'email_path', 'drop', 'human_review']
WE_GOT = ['name', 'email', 'callback', 'title', 'nothing']

SCHEMA = {
    'type': 'object',
    'properties': {
        # NOTE: structured outputs reject 'minimum'/'maximum' on integers
        # (400 invalid_request_error). The 0-10 range is stated in the system
        # prompt, clamped in _clamp() below, and enforced for real by the
        # CHECK constraint on call_scores.
        'outcome_score': {'type': 'integer'},
        'agent_score': {'type': 'integer'},
        'agent_deductions': {'type': 'array', 'items': {'enum': DEDUCTIONS}},
        'what_happened': {'enum': WHAT_HAPPENED},
        'where_it_broke': {'type': 'string'},
        'their_words': {'type': 'string'},
        'we_got': {'type': 'array', 'items': {'enum': WE_GOT}},
        'next_move': {'enum': NEXT_MOVE},
        'needs_human': {'type': 'boolean'},
        # NAMED rules from the agent's own prompt that it broke. Free strings,
        # not an enum: the prompt is operator-edited, and a fixed list would
        # drift away from it silently - which is the bug this whole field
        # exists to fix.
        'rules_violated': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['outcome_score', 'agent_score', 'agent_deductions',
                 'what_happened', 'where_it_broke', 'their_words', 'we_got',
                 'next_move', 'needs_human', 'rules_violated'],
    'additionalProperties': False,
}

RULES_RUBRIC = """

THE AGENT'S OWN PROMPT IS BELOW, exactly as the agent received it for this
call. Score conduct AGAINST THESE RULES, not against a generic idea of a good
call.

  * Read the rules the prompt actually states. Many are NAMED - an Anti-Loop
    Rule, a One-Redirect Rule, a two-turn email gate, answer-and-stop on
    screening questions. Use the prompt's OWN NAME for a rule in
    rules_violated, spelled as the prompt spells it.
  * A rule the prompt does not contain cannot be violated. Do not invent
    rules, and do not import conventions from other scripts you have seen.
  * If the prompt states no named rules, rules_violated is empty. An empty
    list is a real answer.
  * Judge only what the TRANSCRIPT shows. A rule the agent had no occasion to
    apply was not broken.

This matters because "it repeated itself" and "it broke the Anti-Loop Rule"
are different findings. The first suggests the prompt needs rewriting; the
second says the model is ignoring a rule that is already there. Those have
different fixes, and only the second is visible if you name the rule.

AGENT PROMPT (version {version})
--------------------------------
{prompt_text}
--------------------------------
"""

NO_PROMPT_RUBRIC = """

THE AGENT'S PROMPT FOR THIS VERSION IS NOT ON FILE, so rule compliance cannot
be judged. Leave rules_violated EMPTY - an empty list here means "not
assessed", and inventing rule names from a generic idea of good conduct is
exactly what this field exists to replace.
"""

L3_RUBRIC = """
THIS IS AN L3 FOLLOW-UP CALL, NOT A COLD CALL. Score it against a different
bar. We already have the name and the email from an earlier call, and we have
since emailed them. The agent's only job on THIS call is to find out whether
the decision maker actually saw that email, and if so whether there is a good
time to talk.

outcome_score (0-10) for L3:
  10  confirmed they saw it AND got a specific time to reach the decision maker
   7  confirmed they saw it, no time yet
   5  did not see it, but agreed we should resend / got a better address
   3  callback time only
   1  reached a human, learned nothing
   0  no human reached

agent_score for L3 - START AT 10 and deduct the shared faults, PLUS these,
which matter more here than on a cold call:
  reasked_known_info   asked for the name or the email we already have. This
                       is the worst fault an L3 call can commit: it tells the
                       firm nobody was listening the first time. Deduct 4.
  no_name_in_opener    opened without using the contact's name when we had one
  pitched              turned a check-in into a pitch

A polite, brief L3 call that learns "he never saw it" is a GOOD call
(high agent_score) with a middling outcome_score. Score it that way."""

SYSTEM = """You score outbound calls for a service that phones personal-injury
law firms and asks the front desk one question: who handles their demand
letters, and what is that person's email. The agent does not pitch and does
not book anything.

You produce TWO SCORES AND YOU NEVER COMBINE THEM. They measure different
things and averaging them destroys the signal the operator needs.

outcome_score (0-10) - did we get what we called for? This is the BUSINESS
metric. It is allowed to be low on a well-run call; some firms will not tell
you, and that is not the agent's fault.
  10  name + email, spelling confirmed back
   7  name + email, but confirmation was unclear
   5  name only
   3  callback time only
   1  reached a human, got nothing
   0  no human reached

agent_score (0-10) - did OUR side do its job? This is what we CONTROL and it
should reach 9-10 once the script is right. START AT 10 and deduct for:
  rambled                 long turns, wandering, more than ~15 words unprompted
  no_email_ask            never actually asked for an email address
  no_spellback            got an email and did not spell it back to confirm
  talked_over             spoke while the other person was mid-sentence
  sounded_salesy          pitched, described features, argued value
  no_disclosure           was asked if this is AI/a robot and dodged it
  repeated_the_same_ask   asked the same question again after a clear answer
                          or a clear refusal
Deduct roughly 2 points per distinct fault. A call where the receptionist
refuses immediately but the agent was crisp and polite is a LOW outcome_score
and a HIGH agent_score - score it that way.

their_words - THE MOST IMPORTANT FIELD. Quote VERBATIM the single line from
the other person that decided the call: the refusal, the handoff, the
objection. Copy their exact words from the transcript. Do not paraphrase, do
not clean up grammar, do not summarise. If nobody spoke, use an empty string.

where_it_broke - name the turn where it went wrong, in a few words
("right after the opener", "on the what-is-this pushback", "never recovered
from the interruption"). If the call succeeded, say so.

we_got - what we actually banked. A name is a win even if the call otherwise
failed.

needs_human - true only when a person genuinely needs to listen: a complaint,
a legal threat, a compliance problem, an ambiguous DNC request, or a captured
email you would not trust.

You are REPORTING. You do not rewrite the script and you do not propose prompt
changes. A person decides what changes."""


def _clamp(v, lo=0, hi=10):
    """The schema cannot express the bound, so enforce it here rather than
    let a stray 11 hit the CHECK constraint and fail the whole insert."""
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def _price_cents(model: str, in_tok: int, out_tok: int) -> float:
    """Opus 5 list pricing: $5/MTok in, $25/MTok out. Cents."""
    rates = {'claude-opus-5': (5.0, 25.0), 'claude-sonnet-5': (2.0, 10.0),
             'claude-haiku-4-5': (1.0, 5.0)}
    cin, cout = rates.get(model, (5.0, 25.0))
    return round((in_tok / 1e6 * cin + out_tok / 1e6 * cout) * 100, 4)


def _call_row(conn, call_id):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.call_id, c.lead_id, c.stage, c.transcript,
                      c.disconnection_reason, c.duration_ms, c.analysis,
                      l.company, l.dm_name, l.dm_email, l.dm_email_confirmed,
                      -- THE PROMPT ROW FOR THE VERSION THAT ACTUALLY DIALED.
                      --
                      -- prompt_version is an FK to prompt_versions.version (a
                      -- surrogate key), which is why the old query stamped one
                      -- - but it took the NEWEST row for the stage, so scores
                      -- for v15 calls were stamped 17 and every score was
                      -- attributed to a prompt that did not place it. This
                      -- keeps the FK and picks the RIGHT row.
                      pv.version AS prompt_version,
                      c.agent_version AS agent_version_ran,
                      -- and the prompt TEXT of that exact version, so the
                      -- scorer judges the rules the agent was given rather
                      -- than a generic notion of good conduct.
                      pv.prompt_text
                 FROM calls c
                 JOIN leads l ON l.lead_id = c.lead_id
                 LEFT JOIN prompt_versions pv
                        ON pv.agent_id = c.agent_id
                       AND pv.agent_version = c.agent_version
                WHERE c.call_id = %s""", (call_id,))
        return cur.fetchone()


def build_prompt(row) -> str:
    analysis = row.get('analysis') or {}
    extracted = analysis.get('custom_analysis_data') if isinstance(analysis, dict) else {}
    return (
        f"Firm: {row.get('company')}\n"
        f"Stage: {row.get('stage')}\n"
        f"Duration: {round((row.get('duration_ms') or 0)/1000)}s\n"
        f"Disconnection reason: {row.get('disconnection_reason')}\n"
        f"Fields the voice platform extracted: {json.dumps(extracted or {})}\n"
        + (f"ALREADY KNOWN BEFORE THIS CALL (asking for any of it again is "
           f"reasked_known_info): name={row.get('dm_name')!r} "
           f"email={row.get('dm_email')!r}\n" if row.get('stage') == 'L3' else '')
        + "\n"
        f"TRANSCRIPT\n----------\n{row.get('transcript') or '(no transcript - the call never connected)'}\n"
    )


def _rules_section(row) -> str:
    """
    The agent's prompt, for the version that actually dialed.

    Missing prompt text is NOT an error: a version synced before prompt capture,
    or a Retell outage during sync, must not stop a call being scored. It
    degrades to "not assessed" and says so, rather than letting the model fall
    back on a generic rubric and present the result as rule compliance.
    """
    text = (row.get('prompt_text') or '').strip()
    if not text:
        return NO_PROMPT_RUBRIC
    return RULES_RUBRIC.format(version=row.get('agent_version_ran'),
                               prompt_text=text)


def score_call(cfg, call_id: str) -> dict:
    """
    One LLM pass. Writes a call_scores row.

    Raises on failure - the CALLER is responsible for making sure a scoring
    failure never breaks the call flow (see score_pending / drain).
    """
    with db.get_conn() as conn:
        row = _call_row(conn, call_id)
    if row is None:
        raise ValueError(f'no call row for {call_id}')

    client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    resp = client.beta.messages.create(
        model=cfg.SCORER_MODEL,
        max_tokens=4000,
        betas=['server-side-fallback-2026-07-01'],
        # A refusal on a call transcript would otherwise leave the call
        # unscored. The model that actually served is recorded below, so
        # attribution survives a fallback.
        fallbacks='default',
        thinking={'type': 'adaptive'},
        output_config={'effort': 'low',
                       'format': {'type': 'json_schema', 'schema': SCHEMA}},
        # The L3 bar is different: re-asking something we already know is the
        # worst fault on a follow-up and barely registers on a cold call.
        system=(SYSTEM
                + (L3_RUBRIC if row.get('stage') == 'L3' else '')
                + _rules_section(row)),
        messages=[{'role': 'user', 'content': build_prompt(row)}],
    )
    if resp.stop_reason == 'refusal':
        raise RuntimeError(f'scorer refused: {getattr(resp, "stop_details", None)}')

    data = json.loads(next(b.text for b in resp.content if b.type == 'text'))
    served = resp.model
    cost = _price_cents(served, resp.usage.input_tokens, resp.usage.output_tokens)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO call_scores (
                       call_id, lead_id, stage, prompt_version,
                       outcome_score, agent_score, agent_deductions,
                       what_happened, where_it_broke, their_words, we_got,
                       next_move, needs_human, rules_violated, model,
                       input_tokens, output_tokens, cost_cents)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (call_id) DO UPDATE SET
                       -- prompt_version is in here on purpose: re-scoring a
                       -- call must be able to CORRECT an attribution that was
                       -- wrong, not preserve it.
                       prompt_version=EXCLUDED.prompt_version,
                       outcome_score=EXCLUDED.outcome_score,
                       agent_score=EXCLUDED.agent_score,
                       agent_deductions=EXCLUDED.agent_deductions,
                       what_happened=EXCLUDED.what_happened,
                       where_it_broke=EXCLUDED.where_it_broke,
                       their_words=EXCLUDED.their_words,
                       we_got=EXCLUDED.we_got, next_move=EXCLUDED.next_move,
                       needs_human=EXCLUDED.needs_human,
                       rules_violated=EXCLUDED.rules_violated,
                       model=EXCLUDED.model,
                       scored_at=now()""",
                (call_id, row['lead_id'], row['stage'], row['prompt_version'],
                 _clamp(data['outcome_score']), _clamp(data['agent_score']),
                 data['agent_deductions'], data['what_happened'],
                 data['where_it_broke'], data['their_words'], data['we_got'],
                 data['next_move'], data['needs_human'],
                 data.get('rules_violated') or [], served,
                 resp.usage.input_tokens, resp.usage.output_tokens, cost))
            cur.execute(
                """INSERT INTO score_attempts (call_id, attempts, last_error, last_try_at)
                   VALUES (%s, 1, NULL, now())
                   ON CONFLICT (call_id) DO UPDATE
                     SET attempts = score_attempts.attempts + 1,
                         last_error = NULL, last_try_at = now()""", (call_id,))

            # Wire the alert path now (phase 6 fills it): a call that needs a
            # human is surfaced immediately, not at end of day.
            if data['needs_human']:
                cur.execute(
                    """INSERT INTO alerts (lead_id, kind, summary, detail)
                       VALUES (%s, 'needs_human', %s, %s)""",
                    (row['lead_id'],
                     f"{row.get('company')}: {data['what_happened']}",
                     data['their_words']))
    return data


def score_pending(cfg, limit: int = 25) -> dict:
    """
    Score every call that has no score yet.

    Implemented as a queue drain rather than inline in the webhook drain, for
    two reasons: the webhook drain must stay fast, and a transient LLM outage
    must not leave calls permanently unscored. "Every call, not a sample" is a
    property of this loop retrying, not of a single inline attempt.

    NEVER RAISES. A scoring failure must not break the call flow.
    """
    done, failed = 0, 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_id FROM calls c
                    LEFT JOIN call_scores s ON s.call_id = c.call_id
                    LEFT JOIN score_attempts a ON a.call_id = c.call_id
                   WHERE s.call_id IS NULL
                     AND COALESCE(a.attempts, 0) < 4
                   ORDER BY c.created_at
                   LIMIT %s""", (limit,))
            ids = [r['call_id'] for r in cur.fetchall()]

    for cid in ids:
        try:
            score_call(cfg, cid)
            done += 1
        except Exception as exc:
            failed += 1
            try:
                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO score_attempts (call_id, attempts, last_error)
                               VALUES (%s, 1, %s)
                               ON CONFLICT (call_id) DO UPDATE
                                 SET attempts = score_attempts.attempts + 1,
                                     last_error = EXCLUDED.last_error,
                                     last_try_at = now()""",
                            (cid, f'{type(exc).__name__}: {exc}'[:2000]))
            except Exception:
                pass
            print(f'[scorer] {cid} failed: {type(exc).__name__}: {exc}', flush=True)
    return {'scored': done, 'failed': failed}
