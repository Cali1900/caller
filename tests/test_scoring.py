"""
Scoring. EVERY call, two scores, never combined, and it must never break the
call flow.

The LLM is mocked throughout - these tests are about our contract, not about
the model's judgement, and a test suite that costs money per run gets skipped.
"""

import json
from types import SimpleNamespace

import pytest

from api import scorer

LA = 'America/Los_Angeles'


def _fake_response(payload, model='claude-opus-5', stop='end_turn'):
    return SimpleNamespace(
        model=model, stop_reason=stop, stop_details=None,
        content=[SimpleNamespace(type='text', text=json.dumps(payload))],
        usage=SimpleNamespace(input_tokens=300, output_tokens=40))


GOOD = {
    'outcome_score': 10, 'agent_score': 8,
    'agent_deductions': ['talked_over'],
    'what_happened': 'gave_name_and_email',
    'where_it_broke': 'call succeeded',
    'their_words': "No. Actually, I'm not handling that, uh, but Samantha",
    'we_got': ['name', 'email'], 'next_move': 'human_review',
    'needs_human': True,
}


@pytest.fixture
def a_call(db):
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                          pool_status, status)
                       VALUES ('Firm','+15551230001',%s,'active','completed')
                       RETURNING lead_id""", (LA,))
        lead_id = cur.fetchone()['lead_id']
        cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript,
                                          duration_ms, disconnection_reason)
                       VALUES ('c_score','%s','L1','Agent: hi\nUser: not interested.',
                               45000,'user_hangup')""".replace('%s', str(lead_id)))
    db.commit()
    return {'call_id': 'c_score', 'lead_id': lead_id}


@pytest.fixture
def mock_llm(monkeypatch):
    calls = []

    def make(payload=GOOD, **kw):
        def _create(**kwargs):
            calls.append(kwargs)
            return _fake_response(payload, **kw)
        monkeypatch.setattr(
            'anthropic.Anthropic',
            lambda **_: SimpleNamespace(
                beta=SimpleNamespace(messages=SimpleNamespace(create=_create))))
        return calls
    return make


# --------------------------------------------------------------------------
# two scores, never combined
# --------------------------------------------------------------------------

def test_both_scores_are_stored_separately(db, cfg_env, a_call, mock_llm):
    mock_llm()
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute('SELECT outcome_score, agent_score FROM call_scores')
        row = cur.fetchone()
    assert row['outcome_score'] == 10
    assert row['agent_score'] == 8


def test_a_refusal_can_score_low_outcome_and_high_agent(db, cfg_env, a_call, mock_llm):
    """
    The pattern the operator reads for: the script did its job, the firm still
    said no. That must be representable, not averaged away.
    """
    mock_llm({**GOOD, 'outcome_score': 1, 'agent_score': 10,
              'what_happened': 'not_interested', 'we_got': ['nothing'],
              'agent_deductions': [], 'needs_human': False})
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute('SELECT outcome_score, agent_score FROM call_scores')
        row = cur.fetchone()
    assert (row['outcome_score'], row['agent_score']) == (1, 10)


def test_there_is_no_combined_score_column(db):
    """A combined number would destroy the diagnosis. It must not exist."""
    with db.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                        WHERE table_name='call_scores'""")
        cols = {r['column_name'] for r in cur.fetchall()}
    assert not {c for c in cols if 'combined' in c or 'total_score' in c or c == 'score'}


def test_their_words_is_stored_verbatim(db, cfg_env, a_call, mock_llm):
    """The quote is the only thing you can write a better opener against."""
    verbatim = "We we use a software right now, and we don't need this. Thank you."
    mock_llm({**GOOD, 'their_words': verbatim})
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute('SELECT their_words FROM call_scores')
        assert cur.fetchone()['their_words'] == verbatim


def test_out_of_range_scores_are_clamped_not_rejected(db, cfg_env, a_call, mock_llm):
    """The JSON schema cannot express 0-10, so a stray 11 must not fail the row."""
    mock_llm({**GOOD, 'outcome_score': 47, 'agent_score': -3})
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute('SELECT outcome_score, agent_score FROM call_scores')
        row = cur.fetchone()
    assert (row['outcome_score'], row['agent_score']) == (10, 0)


# --------------------------------------------------------------------------
# every call, and never breaking the call flow
# --------------------------------------------------------------------------

def test_score_pending_picks_up_unscored_calls(db, cfg_env, a_call, mock_llm):
    mock_llm()
    assert scorer.score_pending(cfg_env) == {'scored': 1, 'failed': 0}
    assert scorer.score_pending(cfg_env) == {'scored': 0, 'failed': 0}


def test_a_scoring_failure_never_raises(db, cfg_env, a_call, monkeypatch):
    """
    THE RULE: scoring failing must never break the call flow. score_pending is
    what the worker calls, and it must swallow everything.
    """
    def boom(**_):
        raise RuntimeError('LLM is down')
    monkeypatch.setattr('anthropic.Anthropic',
                        lambda **_: SimpleNamespace(
                            beta=SimpleNamespace(messages=SimpleNamespace(create=boom))))
    result = scorer.score_pending(cfg_env)      # must not raise
    assert result == {'scored': 0, 'failed': 1}


def test_a_failure_is_recorded_so_an_outage_is_visible(db, cfg_env, a_call, monkeypatch):
    def boom(**_):
        raise RuntimeError('LLM is down')
    monkeypatch.setattr('anthropic.Anthropic',
                        lambda **_: SimpleNamespace(
                            beta=SimpleNamespace(messages=SimpleNamespace(create=boom))))
    scorer.score_pending(cfg_env)
    with db.cursor() as cur:
        cur.execute('SELECT attempts, last_error FROM score_attempts')
        row = cur.fetchone()
    assert row['attempts'] == 1
    assert 'LLM is down' in row['last_error']


def test_a_poison_call_stops_retrying(db, cfg_env, a_call, monkeypatch):
    def boom(**_):
        raise RuntimeError('always fails')
    monkeypatch.setattr('anthropic.Anthropic',
                        lambda **_: SimpleNamespace(
                            beta=SimpleNamespace(messages=SimpleNamespace(create=boom))))
    for _ in range(6):
        scorer.score_pending(cfg_env)
    with db.cursor() as cur:
        cur.execute('SELECT attempts FROM score_attempts')
        assert cur.fetchone()['attempts'] <= 4


def test_needs_human_raises_an_immediate_alert(db, cfg_env, a_call, mock_llm):
    """Flagged calls do not wait for the end-of-day digest."""
    mock_llm({**GOOD, 'needs_human': True})
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute("SELECT kind, summary FROM alerts WHERE kind='needs_human'")
        assert cur.fetchone() is not None


def test_the_model_that_served_is_recorded(db, cfg_env, a_call, mock_llm):
    """Fallbacks can change the model mid-request; attribution must survive."""
    mock_llm(GOOD, model='claude-opus-4-8')
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute('SELECT model FROM call_scores')
        assert cur.fetchone()['model'] == 'claude-opus-4-8'


def test_the_scorer_never_writes_to_prompt_versions(db, cfg_env, a_call, mock_llm):
    """THE LLM REPORTS. It does not edit the prompt."""
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM prompt_versions')
        before = cur.fetchone()['n']
    mock_llm()
    scorer.score_call(cfg_env, 'c_score')
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM prompt_versions')
        assert cur.fetchone()['n'] == before
    import inspect
    src = inspect.getsource(scorer)
    assert 'UPDATE prompt_versions' not in src
    assert 'INSERT INTO prompt_versions' not in src
