"""
The digest. One email, end of day.

Not a dashboard and not a per-call alert stream - so these tests are about
what the ONE email must contain and must not mislabel.
"""

import pytest

from api import campaigns, digest

LA = 'America/Los_Angeles'


def _scored(db, n, what_happened, outcome, agent, words, deductions=None,
            needs_human=False, prefix='+1555777'):
    with db.cursor() as cur:
        for i in range(n):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status)
                           VALUES (%s,%s,%s,'active','completed') RETURNING lead_id""",
                        (f'Firm {what_happened}{i}', f'{prefix}{abs(hash(what_happened))%900+i:04d}', LA))
            lead_id = cur.fetchone()['lead_id']
            cid = f'c_{what_happened}_{i}'
            cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript,
                                              cost_cents)
                           VALUES (%s,%s,'L1','t', 20.0)""", (cid, lead_id))
            cur.execute("""INSERT INTO call_scores (call_id, lead_id, stage,
                              outcome_score, agent_score, agent_deductions,
                              what_happened, where_it_broke, their_words,
                              we_got, next_move, needs_human, model, cost_cents)
                           VALUES (%s,%s,'L1',%s,%s,%s,%s,'x',%s,%s,'drop',%s,
                                   'claude-opus-5', 1.5)""",
                        (cid, lead_id, outcome, agent, deductions or [],
                         what_happened, words,
                         ['name', 'email'] if 'email' in what_happened else ['nothing'],
                         needs_human))
    db.commit()


def test_a_success_is_never_labelled_a_top_failure(db, cfg_env):
    """
    The digest is read to decide what to change. Calling the one call that
    worked a FAILURE points the operator at the wrong thing.
    """
    _scored(db, 4, 'not_interested', 1, 4, "I'm not interested.")
    _scored(db, 1, 'gave_name_and_email', 10, 8, 'but Samantha', prefix='+1555778')
    body = digest.build(cfg_env)['body']

    failure_section = body.split('WHAT WORKED')[0]
    assert 'gave_name_and_email' not in failure_section
    assert 'TOP FAILURE - not_interested' in body
    assert 'WHAT WORKED' in body
    assert 'gave_name_and_email' in body.split('WHAT WORKED')[1]


def test_verbatim_quotes_appear_under_the_failure(db, cfg_env):
    """The percentage says THAT; the quote says WHAT."""
    quote = "We don't give that out."
    _scored(db, 3, 'not_interested', 1, 5, quote)
    body = digest.build(cfg_env)['body']
    assert f'"{quote}"' in body
    assert 'x3' in body


def test_both_scores_are_reported_and_never_averaged_together(db, cfg_env):
    _scored(db, 2, 'not_interested', 1, 9, 'no thanks')
    body = digest.build(cfg_env)['body']
    assert 'avg agent_score' in body
    assert 'avg outcome_score' in body
    # the two numbers appear separately
    assert '9.0' in body and '1.0' in body


def test_high_agent_low_outcome_is_called_out_as_a_list_problem(db, cfg_env):
    """
    The single most useful reading in the whole digest: the script is fine,
    the list or the ask is wrong.
    """
    _scored(db, 5, 'not_interested', 1, 10, 'not interested')
    body = digest.build(cfg_env)['body']
    assert 'LIST or the ASK' in body


def test_agent_deductions_are_counted(db, cfg_env):
    _scored(db, 3, 'not_interested', 1, 4, 'no',
            deductions=['talked_over', 'sounded_salesy'])
    body = digest.build(cfg_env)['body']
    assert 'AGENT DEDUCTIONS' in body
    assert 'talked_over' in body and 'sounded_salesy' in body


def test_flagged_calls_are_listed_for_a_human(db, cfg_env):
    _scored(db, 1, 'gatekeeper_block', 1, 7, 'legal threat', needs_human=True)
    body = digest.build(cfg_env)['body']
    assert 'flagged for a human' in body


def test_unscored_calls_are_surfaced_not_hidden(db, cfg_env):
    """A silent scorer outage would otherwise look like a quiet day."""
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                          pool_status, status)
                       VALUES ('X','+15557000001',%s,'active','completed')
                       RETURNING lead_id""", (LA,))
        lid = cur.fetchone()['lead_id']
        cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript)
                       VALUES ('c_unscored',%s,'L1','t')""", (lid,))
    db.commit()
    body = digest.build(cfg_env)['body']
    assert 'UNSCORED' in body


def test_it_is_one_email_not_a_dashboard(db, cfg_env):
    _scored(db, 2, 'not_interested', 1, 5, 'no')
    d = digest.build(cfg_env)
    assert set(d.keys()) >= {'subject', 'body', 'stats'}
    assert isinstance(d['body'], str)
    assert len(d['body'].splitlines()) < 80, 'ten minutes to read, not a report'


def test_send_is_idempotent_per_day(db, cfg_env, monkeypatch):
    sends = []
    monkeypatch.setattr('api.mail.send',
                        lambda cfg, to, subject, text: (sends.append(to),
                                                        {'ok': True, 'detail': 'HTTP 201'})[1])
    _scored(db, 1, 'not_interested', 1, 5, 'no')
    assert digest.send(cfg_env)['sent'] is True
    assert digest.send(cfg_env).get('skipped') == 'already sent'
    assert len(sends) == 1, 'a re-run must not double-send'


def test_a_send_failure_is_recorded_and_retryable(db, cfg_env, monkeypatch):
    monkeypatch.setattr('api.mail.send',
                        lambda *a, **k: {'ok': False, 'detail': 'HTTP 500 boom'})
    _scored(db, 1, 'not_interested', 1, 5, 'no')
    r = digest.send(cfg_env)
    assert r['sent'] is False
    with db.cursor() as cur:
        cur.execute('SELECT sent_at, send_error FROM digests')
        row = cur.fetchone()
    assert row['sent_at'] is None
    assert 'boom' in row['send_error']


def test_the_digest_day_is_the_operators_day_not_utcs(db, cfg_env):
    """
    REGRESSION. The day filter compared a timestamptz against a bare ::date,
    which Postgres applies in the SESSION timezone (UTC). Between 17:00 Pacific
    and midnight UTC the calls had already rolled into the next UTC day, so the
    digest for "today" silently omitted them - the end-of-day calls, which are
    the ones worth reading about.

    Two calls on the SAME Pacific day, either side of the UTC boundary.
    """
    from api import digest as d
    la_day = '2026-09-07'
    with db.cursor() as cur:
        for i, when in enumerate(['2026-09-07 23:30:00+00',   # 16:30 Pacific
                                  '2026-09-08 01:30:00+00']):  # 18:30 Pacific
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status)
                           VALUES (%s,%s,%s,'active','completed')
                           RETURNING lead_id""",
                        (f'Boundary {i}', f'+1555999{i:04d}', LA))
            lid = cur.fetchone()['lead_id']
            cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript,
                                              cost_cents, created_at)
                           VALUES (%s,%s,'L1','t',20.0,%s)""",
                        (f'c_boundary_{i}', lid, when))
    db.commit()

    body = d.build(cfg_env, date=la_day)['body']
    assert '2 dialed' in body, f'both Pacific-day calls must count:\n{body[:300]}'
