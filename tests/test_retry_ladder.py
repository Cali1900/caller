"""
RETRY LADDERS.

The bug this replaces: BACKOFF was flat. busy 15m and no_answer 2h whatever
the attempt, so four attempts meant four calls to one firm inside eight
hours. The ladder has to actually escalate, and 'next_day' has to land in the
CALLED PARTY's morning rather than ours.
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
            'stage': 'L1', 'campaign_id': running_campaign_id()}
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

def test_a_rung_is_a_duration_or_next_day_and_nothing_else():
    assert rl.parse('15m') == ('interval', '15 minutes')
    assert rl.parse('4h') == ('interval', '4 hours')
    assert rl.parse('3d') == ('interval', '3 days')
    assert rl.parse('next_day')[0] == 'next_day'
    for bad in ('4 hours', 'tomorrow', '', 'h', '0m', '-2h', 'next day'):
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


def test_next_day_sorts_as_a_day_not_as_zero():
    """It has no number in it. Treated as 0 it would make every ladder
    ending in next_day read as going backwards and be refused."""
    assert rl.minutes('next_day') > rl.minutes('4h')
    assert rl.validate(['15m', '1h', '4h', 'next_day']) == \
        ['15m', '1h', '4h', 'next_day']


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
# next_day, in THEIR timezone
# --------------------------------------------------------------------------

def test_next_day_lands_in_the_called_partys_morning_not_ours(db):
    """
    ⚠️ THE ONE THAT CANNOT BE A PLAIN NUMBER OF HOURS.

    A fixed 24h lands at whatever hour the last attempt fell on - dial at
    19:50 and the retry is 19:50, which the calling window then pushes to the
    following morning, a day later than intended.

    Two leads, same moment, three timezones apart. Each must land at 09:00
    in ITS OWN zone, and the two must be different INSTANTS - if next_day
    were computed once, in the server's zone, both rows would hold the same
    timestamptz.

    Note what is NOT asserted: a fixed three-hour gap. At 04:30 UTC it is
    already tomorrow in New York and still today in Los Angeles, so "the
    next day" is a different calendar day in each. Assuming a constant offset
    is the same one-clock thinking this code exists to avoid.
    """
    campaigns.update(running_campaign_id(), retry_voicemail=['next_day'],
                     max_attempts=5)
    east = _lead(db, phone_e164='+15552250020', timezone=NY)
    west = _lead(db, phone_e164='+15552250021', timezone=LA)
    e = _retry_after(db, east, 1, 'voicemail')
    w = _retry_after(db, west, 1, 'voicemail')

    assert e['next_attempt_at'] != w['next_attempt_at'], \
        'both leads got the same instant - next_day was computed once, in ' \
        'one timezone, rather than in each lead\'s own'

    with db.cursor() as cur:
        for lid, tz in ((east, NY), (west, LA)):
            cur.execute("""SELECT (next_attempt_at AT TIME ZONE %s)::time AS t,
                                  (next_attempt_at AT TIME ZONE %s)::date AS d,
                                  (now() AT TIME ZONE %s)::date AS today
                             FROM leads WHERE lead_id = %s""",
                        (tz, tz, tz, lid))
            r = cur.fetchone()
            assert str(r['t']) == '09:00:00', \
                f'{tz} lead is due at {r["t"]} local, not 09:00'
            assert r['d'] == r['today'] + datetime.timedelta(days=1), \
                f'{tz} lead is due {r["d"]}, not the day after {r["today"]}'


def test_next_day_is_nine_in_the_morning_there(db):
    campaigns.update(running_campaign_id(), retry_voicemail=['next_day'],
                     max_attempts=5)
    lid = _lead(db, phone_e164='+15552250022', timezone=NY)
    _retry_after(db, lid, 1, 'voicemail')
    with db.cursor() as cur:
        cur.execute("""SELECT (next_attempt_at AT TIME ZONE %s)::time AS t
                         FROM leads WHERE lead_id = %s""", (NY, lid))
        assert str(cur.fetchone()['t']) == '09:00:00'


def test_no_caller_data_is_interpolated_into_the_sql():
    """
    BACKOFF interpolated its intervals because the voicemail rule referenced
    l.timezone and a bound interval cannot. Naming the two shapes separately
    is what removed that - so the fragment must carry no values at all.
    """
    for rung in ('15m', '4h', '3d', 'next_day'):
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
    ladder = ['15m', '1h', '4h', 'next_day']
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
    campaigns.update(cid, retry_busy=['15m', '1h', '4h', 'next_day'],
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
    assert _split_ladder('15m, 1h,4h  next_day') == ['15m', '1h', '4h', 'next_day']
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
        'retry_busy': '15m, 1h, 4h, next_day',
        'retry_no_answer': '2h, tomorrow, 1d',
        'retry_voicemail': 'next_day',
    }, follow_redirects=False)
    assert r.status_code == 303
    assert 'REJECTED' in r.headers['location']
    assert list(campaigns.get(cid)['retry_no_answer']) == ['2h', '8h'], \
        'a rejected save changed the ladder anyway'
