"""
RETRY LADDERS.

The bug this replaces: BACKOFF was flat. busy 15m and no_answer 2h whatever
the attempt, so four attempts meant four calls to one firm inside eight
hours. The ladder has to actually escalate.

A RUNG IS A DURATION AND NOTHING ELSE. The 'next_day' rung was removed on
2026-09-09: the calling window is the clamp, not the ladder, so no rung value
can place a call outside the allowed hours and none of them needs to try.
"""
import datetime

import pytest

from api import campaigns, db as dbm, drain, retry_ladder as rl
from tests.conftest import running_campaign_id

NY = 'America/New_York'
LA = 'America/Los_Angeles'


def _lead(db, **kw):
    cols = {'company': 'Whitfield Law', 'phone_e164': '+15552250001',
            'timezone': LA, 'pool_status': 'active', 'status': 'no_answer',
            'has_confirmed_email': False, 'campaign_id': running_campaign_id()}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


def _retry_after(db, lid, attempts, reason):
    """Run the REAL _retry at a given attempt count and read what it set."""
    with db.cursor() as cur:
        cur.execute('UPDATE leads SET attempts=%s WHERE lead_id=%s',
                    (attempts - 1, lid))
    db.commit()
    with dbm.get_conn() as conn:
        drain._retry(conn, str(lid), reason)
    with db.cursor() as cur:
        cur.execute('SELECT attempts, status, next_attempt_at, now() AS n '
                    'FROM leads WHERE lead_id=%s', (lid,))
        return cur.fetchone()


# --------------------------------------------------------------------------
# the grammar
# --------------------------------------------------------------------------

def test_a_rung_is_a_duration_and_nothing_else():
    assert rl.parse('15m') == '15 minutes'
    assert rl.parse('4h') == '4 hours'
    assert rl.parse('3d') == '3 days'
    # 'next_day' was a rung until 2026-09-09 and must now be refused like any
    # other unreadable value - accepting it silently would leave live ladders
    # parsing to nothing after the migration.
    for bad in ('4 hours', 'tomorrow', '', 'h', '0m', '-2h', 'next_day',
                'next day'):
        with pytest.raises(rl.BadLadder):
            rl.parse(bad)


def test_an_unreadable_rung_is_refused_not_defaulted():
    """A rung nobody can parse must not quietly become a gap. That is how a
    firm gets called four times in a morning while the screen says 4h."""
    with pytest.raises(rl.BadLadder):
        rl.validate(['2h', 'sometime', '1d'])


def test_an_empty_ladder_is_refused(db):
    """An empty ladder leaves next_attempt_at unset and the lead is never
    dialed again - a silent drop, not an error."""
    with pytest.raises(rl.BadLadder):
        campaigns.update(running_campaign_id(), retry_busy=[])


def test_a_ladder_that_goes_backwards_is_refused(db):
    """Waits that shrink as attempts rise means calling a firm MORE often the
    longer they have ignored us - the exact pattern the ladder replaced."""
    with pytest.raises(rl.BadLadder):
        campaigns.update(running_campaign_id(), retry_no_answer=['2h', '8h', '1h'])


def test_the_default_ladders_are_valid_and_escalate():
    """The defaults have to survive validate() - a shipped ladder that the
    validator refuses is a campaign that cannot be saved."""
    for outcome, ladder in rl.DEFAULTS.items():
        assert rl.validate(ladder) == ladder, outcome
    assert rl.DEFAULTS['busy'][0] == '15m', 'busy is shortest: a human is there'


def test_the_fallback_rung_is_the_widest_not_the_tightest():
    """An unreadable rung is an UNKNOWN, and unknown must never dial faster
    than configured - the same property worker.next_gap() holds for spacing."""
    assert rl.minutes(rl.FALLBACK_RUNG) >= max(
        rl.minutes(r) for r in rl.DEFAULTS['busy'][:-1])


def test_the_sql_fragment_carries_no_caller_data():
    """One rung shape means one constant fragment. The duration is BOUND."""
    frag, params = rl.sql_for('4h')
    assert frag == 'now() + %s::interval'
    assert params == ['4 hours']
    assert '4' not in frag and 'timezone' not in frag


# --------------------------------------------------------------------------
# escalation - the actual point
# --------------------------------------------------------------------------

def test_the_gap_grows_with_every_attempt(db):
    """
    THE BUG THIS FIXES. Flat 2h at four attempts is four calls to one firm
    in eight hours. Run through the real _retry and assert each wait is
    strictly longer than the one before.
    """
    campaigns.update(running_campaign_id(),
                     retry_no_answer=['2h', '8h', '1d', '3d'], max_attempts=5)
    lid = _lead(db, timezone=LA)
    gaps = []
    for attempt in (1, 2, 3, 4):
        row = _retry_after(db, lid, attempt, 'no_answer')
        assert row['status'] == 'no_answer', f'attempt {attempt} ended the lead early'
        gaps.append(row['next_attempt_at'] - row['n'])
    assert gaps == sorted(gaps) and gaps[0] < gaps[-1], \
        f'the ladder did not escalate: {gaps}'
    assert gaps[0] < datetime.timedelta(hours=3)
    assert gaps[-1] > datetime.timedelta(days=2)


def test_busy_comes_back_soonest(db):
    """A busy signal means a human is there - the best signal in the list."""
    campaigns.update(running_campaign_id(), max_attempts=5)
    b = _retry_after(db, _lead(db, phone_e164='+15552250010'), 1, 'busy')
    n = _retry_after(db, _lead(db, phone_e164='+15552250011'), 1, 'no_answer')
    assert (b['next_attempt_at'] - b['n']) < (n['next_attempt_at'] - n['n'])


def test_the_ladder_holds_at_its_last_rung_rather_than_falling_off(db):
    """A ladder shorter than max_attempts is a thing to warn about, not a
    reason to redial in fifteen minutes."""
    campaigns.update(running_campaign_id(), retry_busy=['15m', '4h'],
                     max_attempts=6)
    lid = _lead(db, phone_e164='+15552250012')
    far = _retry_after(db, lid, 5, 'busy')
    assert (far['next_attempt_at'] - far['n']) > datetime.timedelta(hours=3)


# --------------------------------------------------------------------------
# the ladder does NOT clamp the hour - the calling window does
# --------------------------------------------------------------------------

def test_the_ladder_is_timezone_free(db):
    """
    ⚠️ THIS REPLACES test_next_day_lands_in_the_called_partys_morning_not_ours.

    The 'next_day' rung computed 09:00 tomorrow in the lead's timezone. It was
    removed on 2026-09-09 because the CALLING WINDOW is the clamp: next_attempt_at
    is a NOT-BEFORE gate, and windows.LEGAL_WINDOW / PREFERENCE_WINDOW are ANDed
    into the selection query in the called party's local time. No rung value can
    place a call outside the allowed hours, so no rung needs to try.

    Two leads three timezones apart, same rung, same moment, must now get the
    SAME instant. This is the exact inverse of the old assertion, and it goes
    red the moment anyone reintroduces timezone arithmetic into a rung.
    """
    campaigns.update(running_campaign_id(), retry_voicemail=['1d'],
                     max_attempts=5)
    east = _lead(db, phone_e164='+15552250020', timezone=NY)
    west = _lead(db, phone_e164='+15552250021', timezone=LA)
    e = _retry_after(db, east, 1, 'voicemail')
    w = _retry_after(db, west, 1, 'voicemail')

    delta = abs((e['next_attempt_at'] - w['next_attempt_at']).total_seconds())
    assert delta < 5, (
        'the two leads got different instants for the same rung - a rung is a '
        'duration from now and must not depend on the lead\'s timezone')


def test_a_rung_lands_exactly_one_interval_from_now(db):
    """
    No rounding, no clamping, no shifting to an hour someone considers
    reasonable. The ladder says how long to WAIT; where that lands is the
    calling window's business.
    """
    campaigns.update(running_campaign_id(), retry_voicemail=['4h'],
                     max_attempts=5)
    lid = _lead(db, phone_e164='+15552250022', timezone=NY)
    row = _retry_after(db, lid, 1, 'voicemail')
    with db.cursor() as cur:
        cur.execute("SELECT now() + interval '4 hours' AS want")
        want = cur.fetchone()['want']
    assert abs((row['next_attempt_at'] - want).total_seconds()) < 60


def test_no_caller_data_is_interpolated_into_the_sql():
    """
    BACKOFF interpolated its intervals because the voicemail rule referenced
    l.timezone and a bound interval cannot. With one rung shape there is no
    caller data in the fragment at all - so it must carry no values.
    """
    for rung in ('15m', '4h', '3d', '1d'):
        frag, params = rl.sql_for(rung)
        assert '%s' in frag and params, f'{rung} did not bind its value'
        for tok in ('15', '4', '3', 'minutes', 'hours', 'days', '09:00'):
            assert tok not in frag, f'{tok!r} was interpolated into {frag!r}'


# --------------------------------------------------------------------------
# the rung that could never fire
# --------------------------------------------------------------------------

def test_a_rung_only_counts_if_an_attempt_follows_it():
    """
    At max_attempts=4 the fourth rung of a four-rung ladder never fires: the
    lead is retired after the fourth attempt, so nothing ever waits rung 4.

    An editable value that does nothing is dead surface wearing the costume
    of a setting - the exact fault that made deploy.sh report a pause it was
    not performing.
    """
    ladder = ['15m', '1h', '4h', '1d']
    assert rl.reachable(ladder, 4) == 3
    assert rl.reachable(ladder, 5) == 4
    assert rl.reachable(ladder, 1) == 0


def test_max_attempts_is_the_campaigns_not_a_constant(db):
    campaigns.update(running_campaign_id(), max_attempts=2)
    lid = _lead(db, phone_e164='+15552250030')
    assert _retry_after(db, lid, 2, 'no_answer')['status'] == 'max_attempts'
    campaigns.update(running_campaign_id(), max_attempts=5)
    lid2 = _lead(db, phone_e164='+15552250031')
    assert _retry_after(db, lid2, 2, 'no_answer')['status'] == 'no_answer'


# --------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------

@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_the_screen_says_which_rungs_can_never_fire(client, db):
    """
    VERIFIED AGAINST RENDERED HTML, not a regex over the template. Two
    "alignment checkers" built that way have already both lied.

    A rung that cannot fire is dead surface wearing the costume of a setting.
    It has to be visible on the screen that offers it, or editing it looks
    like it did something.
    """
    cid = running_campaign_id()
    campaigns.update(cid, retry_busy=['15m', '1h', '4h', '1d'],
                     max_attempts=4)
    # Whitespace-normalised: the template wraps, and asserting on the raw
    # bytes would make this test about line length rather than about what
    # the operator reads.
    body = ' '.join(client.get(f'/campaign/{cid}').text.split())
    assert 'can never fire' in body
    assert 'Raise max attempts to 5' in body

    campaigns.update(cid, max_attempts=5)
    body = ' '.join(client.get(f'/campaign/{cid}').text.split())
    assert 'can never fire' not in body, \
        'the warning stayed up after max attempts made every rung reachable'


def test_a_ladder_is_typed_with_commas_or_spaces(client, db):
    cid = running_campaign_id()
    from api.web import _split_ladder
    assert _split_ladder('15m, 1h,4h  1d') == ['15m', '1h', '4h', '1d']
    assert _split_ladder('') == []


def test_saving_a_bad_rung_says_which_one_and_changes_nothing(client, db):
    cid = running_campaign_id()
    campaigns.update(cid, retry_no_answer=['2h', '8h'])
    c = campaigns.get(cid)
    r = client.post(f'/campaign/{cid}/save', data={
        'name': c['name'], 'notes': c['notes'] or '',
        'agent_l1_version': c['agent_l1_version'],
        'sender_email': c['sender_email'], 'sender_name': c['sender_name'],
        'sender_company_line': c['sender_company_line'],
        'daily_cap': c['daily_cap'], 'max_concurrent': c['max_concurrent'],
        'dial_interval_min': c['dial_interval_min'],
        'dial_interval_max': c['dial_interval_max'],
        'max_attempts': c['max_attempts'],
        'retry_busy': '15m, 1h, 4h, 1d',
        'retry_no_answer': '2h, tomorrow, 1d',
        'retry_voicemail': '1d',
    }, follow_redirects=False)
    assert r.status_code == 303
    assert 'REJECTED' in r.headers['location']
    assert list(campaigns.get(cid)['retry_no_answer']) == ['2h', '8h'], \
        'a rejected save changed the ladder anyway'
