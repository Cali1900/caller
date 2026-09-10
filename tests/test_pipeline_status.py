"""
THE PIPELINE STATUSES.

The system advances a lead through stages it can observe; a person moves it
anywhere. That asymmetry is the design, and most of these tests are about the
system NOT moving something it should leave alone.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, clicks, db as dbm, pipeline, stages, web


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _lead(status='new', phone='+14245558100', has_confirmed_email=True, email='b@f.example'):
    cid = c.create('PS-' + phone[-4:])['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, has_confirmed_email, status)
                           VALUES (%s,'W','Bob',%s,true,'America/Los_Angeles',
                                   %s,%s,%s) RETURNING lead_id""",
                        (phone, email, cid, has_confirmed_email, status))
            return cur.fetchone()['lead_id']


def _status(lid):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM leads WHERE lead_id=%s', (lid,))
            return cur.fetchone()['status']


# ---------------------------------------------------------------------------
# all seven are settable by hand
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('st', ['emailed', 'engaged', 'demo_booked', 'won',
                                'lost', 'lost_no_response', 'bad_email'])
def test_each_new_status_is_settable_by_hand(db, client, st):
    lid = _lead(phone='+142455581' + str(abs(hash(st)) % 90 + 10))
    r = client.post(f'/leads/{lid}/status', data={'status': st},
                    follow_redirects=False)
    assert 'REJECTED' not in r.headers['location']
    assert _status(lid) == st


def test_dnc_and_dialing_are_still_excluded(db, client):
    """A lead that READS suppressed and is still dialable is worse than no DNC
    feature; a hand-set `dialing` strands it forever."""
    assert 'dnc' not in web.MANUAL_STATUSES
    assert 'dialing' not in web.MANUAL_STATUSES
    lid = _lead(phone='+14245558199')
    for bad in ('dnc', 'dialing'):
        r = client.post(f'/leads/{lid}/status', data={'status': bad},
                        follow_redirects=False)
        assert 'REJECTED' in r.headers['location']
    assert _status(lid) == 'new'


# ---------------------------------------------------------------------------
# the system advances, never retreats
# ---------------------------------------------------------------------------

def test_sending_email_1_advances_to_emailed(db):
    lid = _lead(status='completed', phone='+14245558200')
    stages.mark_emailed(lid, emailed_by='operator')
    assert _status(lid) == 'emailed'


def test_a_click_advances_to_clicked_not_engaged(db, client):
    """
    ⚠️ CHANGED 2026-09-10: `engaged` means they REPLIED. A click is interest, not
    an answer - the standing rule since click tracking was built - and once drip
    membership became derived from status, recording a click as `engaged` ENDED
    THE SEQUENCE for a firm that had just read the sample.

    `clicked` is its own rung between `emailed` and `engaged`, so the ladder stays
    forward-only and a reply still outranks a click.
    """
    lid = _lead(status='emailed', phone='+14245558201')
    client.get(f'/c/{clicks.token_for(lid)}', follow_redirects=False)
    assert _status(lid) == 'clicked'


def test_a_reply_outranks_a_click_on_the_ladder(db, client):
    """clicked -> engaged is forward; engaged -> clicked is not."""
    from api import pipeline
    lid = _lead(status='clicked', phone='+14245558299')
    assert pipeline.may_advance('clicked', 'engaged') is True
    assert pipeline.may_advance('engaged', 'clicked') is False
    assert pipeline.may_advance('emailed', 'clicked') is True


def test_a_click_never_drags_a_booked_lead_backwards(db, client):
    """The asymmetry. Forward only."""
    lid = _lead(status='demo_booked', phone='+14245558202')
    client.get(f'/c/{clicks.token_for(lid)}', follow_redirects=False)
    assert _status(lid) == 'demo_booked'


@pytest.mark.parametrize('frozen', ['human_review', 'dnc', 'lost',
                                    'lost_no_response', 'bad_email'])
def test_the_system_never_touches_a_frozen_status(db, client, frozen):
    """
    human_review is the scorer asking for a PERSON - advancing past it would
    silently clear the one flag that says look at this. dnc is compliance. The
    lost/bad_email statuses are conclusions: a stray click on a bounced address
    is not a reason to declare the lead engaged again.
    """
    lid = _lead(status=frozen, phone='+142455583' + str(abs(hash(frozen)) % 90 + 10))
    client.get(f'/c/{clicks.token_for(lid)}', follow_redirects=False)
    assert _status(lid) == frozen


def test_a_hand_set_status_is_not_overwritten_by_a_later_click(db, client):
    """Sean overrules the system, and it stays overruled."""
    lid = _lead(status='emailed', phone='+14245558204')
    client.post(f'/leads/{lid}/status', data={'status': 'lost'},
                follow_redirects=False)
    client.get(f'/c/{clicks.token_for(lid)}', follow_redirects=False)
    assert _status(lid) == 'lost'


def test_an_automatic_move_is_distinguishable_from_a_hand_one(db, client):
    lid = _lead(status='completed', phone='+14245558205')
    stages.mark_emailed(lid, emailed_by='operator')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT summary FROM activity
                            WHERE lead_id=%s AND kind='status'""", (lid,))
            rows = [r['summary'] for r in cur.fetchall()]
    assert any('by the system' in s for s in rows)
    assert not any('BY HAND' in s for s in rows)


# ---------------------------------------------------------------------------
# the forecast reads the real stage
# ---------------------------------------------------------------------------

def test_the_forecast_reads_status_not_a_derivation(db):
    """
    Deriving 'engaged' from clicked-or-replied could never express a stage
    nobody can compute - a demo booked in a phone call the app never saw.
    """
    from api import forecast
    cid = c.create('PS-fc')['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            for phone, st in (('+14245558300', 'demo_booked'),
                              ('+14245558301', 'engaged'),
                              ('+14245558302', 'emailed'),
                              ('+14245558303', 'lost')):
                cur.execute("""INSERT INTO leads (phone_e164, company, timezone,
                                   campaign_id, status, demands_per_month)
                               VALUES (%s,'W','America/Los_Angeles',%s,%s,10)""",
                            (phone, cid, st))
    out = forecast.build(cid)
    by = {r['stage']: r for r in out['rows']}
    assert by['demo_booked']['leads'] == 1
    assert by['engaged']['leads'] == 1
    assert by['emailed']['leads'] == 1
    assert by['unweighted']['leads'] == 1, 'lost weighs nothing'
    assert out['total_weighted'] == pytest.approx(
        10 * 150 * 0.40 + 10 * 150 * 0.15 + 10 * 150 * 0.03)


def test_won_weighs_with_demo_booked_not_as_a_fifth_stage(db):
    """A closed deal is not a forecast."""
    from api import forecast
    cid = c.create('PS-won')['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, timezone,
                               campaign_id, status, demands_per_month)
                           VALUES ('+14245558400','W','America/Los_Angeles',
                                   %s,'won',10)""", (cid,))
    by = {r['stage']: r for r in forecast.build(cid)['rows']}
    assert by['demo_booked']['leads'] == 1


def test_the_new_statuses_are_filterable(db, client):
    lid = _lead(status='engaged', phone='+14245558500')
    assert str(lid) in client.get('/?status=engaged').text
    assert str(lid) not in client.get('/?status=won').text
    assert client.get('/funnel?status=engaged').status_code == 200


# ---------------------------------------------------------------------------
# leaving the archive is not a status change
# ---------------------------------------------------------------------------

def test_the_status_dropdown_refuses_to_take_a_lead_out_of_archive(db, client):
    """
    THE MIRROR OF THE REFUSAL FOR MOVING *INTO* ARCHIVED.

    Setting an archived lead to 'new' from the dropdown used to run a bare
    UPDATE. That leaves pool_status='done', campaign_id NULL, archived_at and
    returns_at set, and every gate the return exists to clear still set -
    un-archived in name only, undialable and un-emailable.

    And PERMANENTLY so, two ways: archive.return_due() selects
    WHERE status='archived', so the nightly sweep can no longer see it; and
    lead.html renders "Return to the pool now" only for an archived lead, so
    the one control that would repair it disappears. No route back but SQL.

    It REFUSES rather than performing the restore quietly. unarchive()
    snapshots the send record and clears four gates - far more than "set the
    status" - and a dropdown that silently did all that would make the timeline
    lie about what a person did. Same discipline as /dnc being the only route
    to suppression.
    """
    from api import archive
    lid = _lead(phone='+14245558210')
    archive.archive(lid, 'refused', by='test')

    r = client.post(f'/leads/{lid}/status', data={'status': 'new'},
                    follow_redirects=False)
    assert 'REJECTED' in r.headers['location'], \
        'the dropdown took a lead out of archive and stranded it'
    assert 'Return+to+the+pool' in r.headers['location'] \
        or 'Return%20to%20the%20pool' in r.headers['location'], \
        'the refusal must name the control that actually works'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT status, pool_status, archived_at, returns_at
                             FROM leads WHERE lead_id = %s""", (lid,))
            row = cur.fetchone()
    assert row['status'] == 'archived', 'the lead must be left alone entirely'
    assert row['pool_status'] == 'done'
    assert row['archived_at'] is not None and row['returns_at'] is not None


def test_the_restore_button_is_the_route_out_and_it_works(db, client):
    """
    The other half: refusing is only defensible because a control that does the
    whole thing exists. It is the SAME code the nightly sweep runs.
    """
    from api import archive
    lid = _lead(phone='+14245558211')
    archive.archive(lid, 'refused', by='test')

    r = client.post(f'/leads/{lid}/unarchive', data={'unarchived_by': 'sean'},
                    follow_redirects=False)
    assert r.status_code == 303

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT status, pool_status, campaign_id, attempts,
                                  archived_at, returns_at, has_confirmed_email,
                                  emailed_at, replied_at, dm_email, dm_name
                             FROM leads WHERE lead_id = %s""", (lid,))
            row = cur.fetchone()
    assert row['status'] == 'new'
    assert row['pool_status'] == 'pool'
    assert row['campaign_id'] is None, 'so it can be assigned again'
    assert row['attempts'] == 0
    assert row['archived_at'] is None and row['returns_at'] is None
    # The gates are cleared...
    assert row['has_confirmed_email'] is False
    assert row['emailed_at'] is None and row['replied_at'] is None
    # ...and the FACTS are kept.
    assert row['dm_email'] == 'b@f.example'
    assert row['dm_name'] == 'Bob'

    # The return is on the timeline, not silent.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT summary FROM activity
                            WHERE lead_id = %s AND kind = 'archive'
                            ORDER BY created_at DESC LIMIT 1""", (lid,))
            assert 'sean' in cur.fetchone()['summary']
