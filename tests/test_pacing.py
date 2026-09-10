"""
SENDING PACE: a gap, an hourly cap, a daily cap, business hours.

⚠️ EVERY LAYER GATES SELECTION, NEVER THE SEND. A held lead is not-yet-due: no
audit row, no refusal, no status to get stuck in. Putting these in send_step()
would write a "refused" row for every waiting lead on every tick - thousands of
entries that read as failures for mail that is simply queued behind a cap.

So each test here asserts the SAME shape: the lead is scheduled, it is eligible
in every other respect, and drip.due() does not return it yet - and after the
constraint lifts, it does.
"""
import itertools

import pytest
from fastapi.testclient import TestClient

from api import campaigns, db as dbm, drip, worker
from tests.test_drip import dripc, _lead, _join, open_all_hours, LA  # noqa: F401


@pytest.fixture
def client(db, cfg_env):
    import api.web as web            # noqa: F401
    from api.main import app
    return TestClient(app)


_phone = itertools.count(1)


def _mk(db, **kw):
    """
    A lead with a UNIQUE phone. leads_phone_uniq is a real constraint and
    test_drip's helper defaults every lead to one number, so a test needing two
    leads has to say so - which is the point of these tests: pacing is only
    observable with more than one lead due at once.
    """
    kw.setdefault('phone_e164', f'+1555444{next(_phone):04d}')
    kw.setdefault('days_ago', 11)
    return _lead(db, **kw)


def _set(cid, **cols):
    sets = ', '.join(f'{k} = %s' for k in cols)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f'UPDATE campaign_configs SET {sets} WHERE campaign_id = %s',
                        list(cols.values()) + [cid])


def _windows(cid, enabled=True, start='00:00', end='23:59'):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE campaign_windows
                              SET enabled = %s, start_time = %s, end_time = %s
                            WHERE campaign_id = %s""",
                        (enabled, start, end, cid))


def _sent(lid, when='now()', seq=99):
    """A send that COUNTS against the caps: sent_at is what both caps read."""
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            # sent_by alongside sent_at - email_sends_sent_is_attributed.
            cur.execute(f"""INSERT INTO email_sends
                                (lead_id, seq, to_email, sent_at, sent_by,
                                 click_token)
                            VALUES (%s, %s, 'x@y.test', {when}, 'operator', %s)""",
                        (lid, seq, f'tok-cap-{lid}-{seq}'))


def _due_ids(cid=None):
    return {r['lead_id'] for r in drip.due(limit=500)}


# ==========================================================================
# the caps
# ==========================================================================

def test_the_hourly_cap_holds_the_rest(db, dripc):
    """
    ⚠️ THE HOURLY CAP IS THE REAL THROTTLE. The gap alone permits 60/hour at its
    tightest, and the target is ~12-15, so this is the layer that paces.
    """
    cid = dripc['campaign_id']
    _set(cid, email_hourly_cap=1, email_daily_cap=250)
    a, b = _mk(db), _mk(db)
    _join(db, a, cid); _join(db, b, cid)
    assert {a, b} <= _due_ids(), 'both should be due before anything is sent'

    _sent(a)                          # one send, this hour, cap is 1
    assert _due_ids() == set(), 'the hourly cap did not hold the second lead'

    _set(cid, email_hourly_cap=15)
    assert {a, b} <= _due_ids(), 'raising the cap did not release them'


def test_the_daily_cap_holds_the_rest(db, dripc):
    """
    The volume ceiling, and the warm-up dial. Hourly is set wide so ONLY the
    daily cap can be biting - the point of the test is which layer holds.
    """
    cid = dripc['campaign_id']
    _set(cid, email_hourly_cap=60, email_daily_cap=1)
    a, b = _mk(db), _mk(db)
    _join(db, a, cid); _join(db, b, cid)
    _sent(a)
    assert _due_ids() == set(), "the daily cap did not hold today's remainder"

    _set(cid, email_daily_cap=50)
    assert {a, b} <= _due_ids()


def test_the_caps_count_per_mailbox_not_per_campaign(db, dripc):
    """
    ⚠️ REPUTATION BELONGS TO THE ADDRESS. Two drips on info@counselorai.io at
    15/hour each would put 30/hour on one mailbox. So the count spans every
    campaign sharing sender_email, and the tighter campaign is bound by the
    shared total - conservative on purpose, because the other wrong answer
    costs a domain.
    """
    cid = dripc['campaign_id']
    other = campaigns.create('DRIP-OTHER', campaign_type='drip')['campaign_id']
    campaigns.start(other)
    open_all_hours(other)
    drip.save_steps(other, [{'delay_days': 0, 'subject': 's', 'body': 'b'}])
    _set(other, sender_email=dripc['sender_email'])
    _set(cid, email_hourly_cap=1, email_daily_cap=250)

    mine = _mk(db)
    _join(db, mine, cid)
    assert mine in _due_ids()

    # A send on the OTHER campaign, same mailbox, spends this one's budget.
    theirs = _mk(db)
    _join(db, theirs, other)
    _sent(theirs)
    assert mine not in _due_ids(), \
        'a send from another campaign on the same mailbox did not count'


def test_a_different_mailbox_does_not_spend_this_ones_budget(db, dripc):
    """The other half: per-mailbox must not become per-everything."""
    cid = dripc['campaign_id']
    other = campaigns.create('DRIP-ELSEWHERE', campaign_type='drip')['campaign_id']
    campaigns.start(other)
    open_all_hours(other)
    drip.save_steps(other, [{'delay_days': 0, 'subject': 's', 'body': 'b'}])
    _set(other, sender_email='someone-else@counselorai.io')
    _set(cid, email_hourly_cap=1, email_daily_cap=250)

    mine = _mk(db)
    _join(db, mine, cid)
    theirs = _mk(db)
    _join(db, theirs, other)
    _sent(theirs)
    assert mine in _due_ids(), \
        'a send from a DIFFERENT mailbox was counted against this one'


# ==========================================================================
# business hours, in the CONTACT's timezone
# ==========================================================================

def test_a_step_outside_business_hours_waits(db, dripc):
    """
    ⚠️ REUSES windows.PREFERENCE_WINDOW, the call-side logic, keyed on the drip
    campaign. Not a second implementation: two copies of a timezone rule are two
    rules, and the first time they disagree one of them is mailing a firm at 4am.
    """
    cid = dripc['campaign_id']
    lid = _mk(db)
    _join(db, lid, cid)
    assert lid in _due_ids()

    _windows(cid, enabled=False)          # no window matches any hour
    assert lid not in _due_ids(), 'a step sent outside business hours'

    _windows(cid, enabled=True)
    assert lid in _due_ids(), 'reopening the window did not release it'


def test_business_hours_are_read_in_the_FIRMS_timezone(db, dripc):
    """
    Two leads, same drip, same window, timezones ~20 hours apart. The window is
    a narrow slot around the OPERATOR's local time, so it can only contain one
    of them - and which one proves whose clock is being read.
    """
    cid = dripc['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT (now() AT TIME ZONE %s)::time AS t", (LA,))
            local = cur.fetchone()['t']
    # A slot from the LEAD's local now to the end of its local day: it contains
    # Los Angeles by construction, and cannot contain Auckland, which is ~19
    # hours ahead and therefore already tomorrow.
    lo = local.replace(second=0, microsecond=0)
    _windows(cid, start=lo.strftime('%H:%M'), end='23:59')

    here = _mk(db, timezone=LA)
    far = _mk(db, timezone='Pacific/Auckland')
    _join(db, here, cid); _join(db, far, cid)
    ids = _due_ids()
    assert here in ids, 'the lead inside its own business hours was held'
    assert far not in ids, \
        'the window was read in OUR timezone, not the firm\'s'


def test_a_lead_with_no_timezone_falls_back_and_never_vanishes(db, dripc):
    """
    ⚠️ leads.timezone IS NULLABLE since the email-only import, and
    `now() AT TIME ZONE NULL` is NULL - which fails every comparison. Without the
    coalesce an imported lead would never be due and would never say why: the
    exact silent-vanishing failure this feature exists to prevent.
    """
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, timezone=None, phone_e164=None,
                lead_source='import')
    _join(db, lid, cid)
    assert lid in _due_ids(), \
        'a lead with no timezone vanished from the schedule instead of ' \
        "falling back to the operator's hours"


# ==========================================================================
# the digest, the backlog, the gap
# ==========================================================================

def test_the_digest_still_shows_what_a_cap_is_holding(db, dripc):
    """
    ⚠️ upcoming() MUST IGNORE PACING. It answers "what is SCHEDULED", and a cap
    shifts when a mail goes out without changing whether it is coming. A digest
    that hid capped sends would under-report tomorrow - the one thing that
    checkpoint exists to prevent, given the reply gate cannot fail closed.
    """
    cid = dripc['campaign_id']
    _set(cid, email_hourly_cap=1, email_daily_cap=1)
    a, b = _mk(db), _mk(db)
    _join(db, a, cid); _join(db, b, cid)
    _sent(a)
    assert _due_ids() == set(), 'the caps are not holding, so this proves nothing'
    listed = {r['lead_id'] for r in drip.upcoming(within_hours=48)}
    assert {a, b} <= listed, 'the digest hid sends that a cap is holding'


def test_held_reports_the_backlog_and_which_layer_holds_it(db, dripc):
    cid = dripc['campaign_id']
    _set(cid, email_hourly_cap=1, email_daily_cap=250)
    a, b, c = _mk(db), _mk(db), _mk(db)
    for lid in (a, b, c):
        _join(db, lid, cid)
    _sent(a)
    h = drip.held(cid)
    assert h['scheduled'] == 3, h
    assert h['sendable'] == 0, h
    assert h['held'] == 3, h
    assert h['over_hourly'] == 3, h
    assert h['outside_hours'] == 0, h
    assert h['sent_hour'] == 1 and h['hourly_cap'] == 1, h


def test_the_gap_is_jittered_and_within_the_configured_range(db, dripc):
    """
    Same shape as next_gap() for dials, and the range comes from the campaign -
    not from a constant that nothing reads, which is how masked-guard row 9
    happened.
    """
    _set(dripc['campaign_id'], email_gap_min_seconds=100,
         email_gap_max_seconds=200)
    rolls = [worker.next_email_gap() for _ in range(25)]
    assert all(100 <= r <= 200 for r in rolls), sorted(rolls)[:3]
    assert len(set(round(r) for r in rolls)) > 1, 'the gap is not jittered'


def test_the_gap_falls_back_WIDE_when_no_drip_is_running(db, dripc):
    """
    FAILS SLOW, NEVER FAST. An unreadable or absent configuration must never be
    able to TIGHTEN the interval between sends.
    """
    campaigns.stop(dripc['campaign_id'])
    # ⚠️ LITERAL NUMBERS, NOT worker.FALLBACK_EMAIL_GAP. Reading the constant to
    # bound the assertion made this test unable to fail: break 130 set the
    # fallback to (1, 2) seconds, the test read (1, 2), and reported GREEN. A
    # TEST THAT READS THE VALUE IT IS ASSERTING ASSERTS NOTHING - the same shape
    # as masked-guard row 9, turned inward.
    #
    # 60 seconds is the floor that matters: the danger of an unknown
    # configuration is sending FASTER than anyone chose, never slower.
    rolls = [worker.next_email_gap() for _ in range(10)]
    assert all(r >= 60 for r in rolls), \
        f'the fallback gap can send faster than 60s apart: {sorted(rolls)[:3]}'
    assert all(r <= 3600 for r in rolls), sorted(rolls)[-3:]
    assert worker.FALLBACK_EMAIL_GAP[0] >= 60, worker.FALLBACK_EMAIL_GAP


def test_one_email_per_tick_not_a_batch(db, dripc):
    """
    The worker asks for ONE. `limit=50` was a query-cost decision doing duty as
    a rate, and it let 50 emails share a few seconds.
    """
    cid = dripc['campaign_id']
    for _ in range(4):
        _join(db, _mk(db), cid)
    assert len(drip.due(limit=1)) == 1, 'due() ignored the limit the worker sets'
    assert len(drip.due(limit=50)) == 4


# ==========================================================================
# the screen
# ==========================================================================

def test_a_drip_campaign_can_save_its_config(db, dripc, client):
    """
    ⚠️ THIS WAS A 422 FOR AS LONG AS DRIP CAMPAIGNS HAVE EXISTED. daily_cap and
    the other call fields were required Form(...) params, and the drip screen
    renders none of them - so renaming a drip or changing its sender was
    impossible. Found while adding the pace fields to the same form.
    """
    cid = dripc['campaign_id']
    r = client.post(f'/campaign/{cid}/save', data={
        'name': 'DRIP-RENAMED', 'notes': 'n',
        'sender_email': dripc['sender_email'], 'sender_name': 'Sean',
        'sender_company_line': 'CounselorAI',
        'email_hourly_cap': '12', 'email_daily_cap': '40',
        'email_gap_min_seconds': '90', 'email_gap_max_seconds': '240',
    }, follow_redirects=False)
    assert r.status_code == 303, r.text[:400]
    assert 'REJECTED' not in r.headers['location'], r.headers['location']
    got = campaigns.get(cid)
    assert got['name'] == 'DRIP-RENAMED'
    assert got['email_hourly_cap'] == 12 and got['email_daily_cap'] == 40
    assert got['email_gap_min_seconds'] == 90


def test_the_day_ceiling_is_refused_above_250(db, dripc, client):
    """A new domain earns volume; it cannot be given it. The CHECK agrees."""
    cid = dripc['campaign_id']
    r = client.post(f'/campaign/{cid}/save', data={
        'name': 'DRIP-T', 'notes': '', 'sender_email': dripc['sender_email'],
        'sender_name': 'Sean', 'sender_company_line': 'CounselorAI',
        'email_hourly_cap': '15', 'email_daily_cap': '5000',
        'email_gap_min_seconds': '60', 'email_gap_max_seconds': '300',
    }, follow_redirects=False)
    assert 'REJECTED' in r.headers['location'], r.headers['location']
    assert campaigns.get(cid)['email_daily_cap'] != 5000


def test_the_campaign_screen_shows_the_backlog_and_the_controls(db, dripc, client):
    cid = dripc['campaign_id']
    _set(cid, email_hourly_cap=1)
    a, b = _mk(db), _mk(db)
    _join(db, a, cid); _join(db, b, cid)
    _sent(a)
    page = client.get(f'/campaign/{cid}').text
    for name in ('email_hourly_cap', 'email_daily_cap',
                 'email_gap_min_seconds', 'email_gap_max_seconds'):
        assert f'name="{name}"' in page, f'{name} is not editable on the screen'
    assert 'waiting on pace' in page, 'the backlog is not shown'
    assert 'Nothing failed and nothing is lost' in page, \
        'a held send must not read as a failure'


# ==========================================================================
# the constraint that makes duplicate positions impossible
# ==========================================================================

def test_the_DATABASE_refuses_two_live_steps_at_one_position(db, dripc):
    """
    ⚠️ THE HANDLER IS NOT THE GUARD - THE INDEX IS. drip_steps_position is
    UNIQUE (campaign_id, position) WHERE deleted_at IS NULL, so a duplicate live
    position is impossible whatever save_steps does, and impossible from psql too.

    Nothing asserted that until now, which meant a future migration could drop it
    and leave the handler as the only thing standing between an editor bug and a
    sequence with two step 2s. This is that assertion.

    ⚠️ AND WHY IT IS PARTIAL. A plain UNIQUE (campaign_id, position) cannot exist
    here: soft-deleted rows KEEP their position, because a step that has been sent
    keeps its record after its copy changes. Remove step 2, add a new step 2, and
    the table legitimately holds two rows at position 2 - one deleted, one live.
    A plain constraint could not even be CREATED on that table, let alone survive
    the next save. The partial predicate is what expresses "one LIVE step per
    position" without forbidding history.
    """
    import psycopg2
    cid = dripc['campaign_id']
    live = drip.steps(cid)
    assert live, 'the fixture should have steps'

    with pytest.raises(psycopg2.errors.UniqueViolation):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO drip_steps
                           (campaign_id, position, delay_days, subject, body,
                            enabled)
                       VALUES (%s, %s, 99, 'DUP', 'dup', true)""",
                    (cid, live[0]['position']))


def test_history_at_the_same_position_is_allowed_and_is_not_corruption(db, dripc):
    """
    The other half, and the one that was mistaken for a bug: remove a step, add
    another at the same position, and an unfiltered query shows TWO rows there.
    That is history. drip.steps() reads `deleted_at IS NULL`, so the editor sees
    exactly one - and the count of rows in the table is not a measure of anything.
    """
    cid = dripc['campaign_id']
    before = drip.steps(cid)
    keep = before[0]
    # rewrite the sequence as one step, then two: the second position's old row
    # is soft-deleted and a new one takes its place.
    drip.save_steps(cid, [{'step_id': keep['step_id'], 'delay_days': 0,
                           'delay_minutes': 0, 'subject': keep['subject'],
                           'body': keep['body'], 'enabled': True}])
    drip.save_steps(cid, [{'step_id': keep['step_id'], 'delay_days': 0,
                           'delay_minutes': 0, 'subject': keep['subject'],
                           'body': keep['body'], 'enabled': True},
                          {'delay_days': 5, 'subject': 'NEW TWO', 'body': 'n2'}])
    assert [s['position'] for s in drip.steps(cid)] == [1, 2]
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM drip_steps
                            WHERE campaign_id = %s AND position = 2""", (cid,))
            at_two = cur.fetchone()['n']
    assert at_two > 1, 'the deleted row at position 2 should still be there'
    assert len([s for s in drip.steps(cid) if s['position'] == 2]) == 1, \
        'the editor must see exactly one live step at position 2'
