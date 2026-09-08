"""
THE OPERATOR OVERRULES THE SYSTEM.

A lead the scorer flagged human_review used to be stuck: nothing but the scorer
could set status, so a flag Sean had already dealt with held the lead forever.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm, web


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _lead(status='human_review', phone='+14245556001'):
    cid = c.create('ST-' + phone[-4:])['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, timezone,
                                              campaign_id, status)
                           VALUES (%s,'W','America/Los_Angeles',%s,%s)
                           RETURNING lead_id""", (phone, cid, status))
            return cur.fetchone()['lead_id']


def _status(lid):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM leads WHERE lead_id=%s', (lid,))
            return cur.fetchone()['status']


def test_a_human_review_lead_can_be_cleared_by_hand(db, client):
    """THE POINT. Without this the flag holds forever."""
    lid = _lead('human_review')
    r = client.post(f'/leads/{lid}/status', data={'status': 'completed'},
                    follow_redirects=False)
    assert r.status_code == 303
    assert 'REJECTED' not in r.headers['location']
    assert _status(lid) == 'completed'


@pytest.mark.parametrize('st', web.MANUAL_STATUSES)
def test_every_offered_status_can_actually_be_set(db, client, st):
    """A dropdown that offers a value the route refuses is a dropdown that
    lies."""
    lid = _lead('new', '+1424555' + str(6100 + abs(hash(st)) % 800))
    client.post(f'/leads/{lid}/status', data={'status': st},
                follow_redirects=False)
    assert _status(lid) == st


def test_the_change_lands_on_the_timeline_as_a_HAND_change(db, client):
    """
    A hand correction must never be mistakable for an agent capture.
    """
    lid = _lead('human_review', '+14245556002')
    client.post(f'/leads/{lid}/status',
                data={'status': 'completed', 'changed_by': 'sean'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT kind, summary, detail FROM activity
                            WHERE lead_id=%s AND kind='status'""", (lid,))
            row = cur.fetchone()
    assert row is not None, 'a hand change must be recorded'
    assert 'BY HAND' in row['summary']
    assert 'human_review -> completed' in row['detail'], 'from what, to what'
    assert 'sean' in row['detail'], 'who'


def test_dnc_cannot_be_set_from_the_dropdown(db, client):
    """
    Setting status='dnc' alone would leave a lead that LOOKS suppressed and is
    still dialable. Suppression is the highest-liability object here and does
    not get a shortcut - /dnc writes the suppression row in the same
    transaction.
    """
    lid = _lead('new', '+14245556003')
    r = client.post(f'/leads/{lid}/status', data={'status': 'dnc'},
                    follow_redirects=False)
    assert 'REJECTED' in r.headers['location']
    assert _status(lid) == 'new', 'nothing may change'
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) AS n FROM suppression')
            n = cur.fetchone()['n']
    assert n == 0, 'and nothing may be suppressed by a status change'


def test_the_dnc_button_still_suppresses(db, client):
    """The path that DOES set dnc must also write suppression, in one go."""
    lid = _lead('new', '+14245556004')
    client.post(f'/leads/{lid}/dnc', follow_redirects=False)
    assert _status(lid) == 'dnc'
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM suppression WHERE phone_e164=%s",
                        ('+14245556004',))
            assert cur.fetchone()['n'] == 1


def test_dialing_cannot_be_set_by_hand(db, client):
    """A transient claim state owned by the dialer. Set by hand it strands the
    lead: claimed forever, never selected again."""
    lid = _lead('new', '+14245556005')
    r = client.post(f'/leads/{lid}/status', data={'status': 'dialing'},
                    follow_redirects=False)
    assert 'REJECTED' in r.headers['location']
    assert _status(lid) == 'new'


def test_an_invented_status_is_refused(db, client):
    lid = _lead('new', '+14245556006')
    r = client.post(f'/leads/{lid}/status', data={'status': 'banana'},
                    follow_redirects=False)
    assert 'REJECTED' in r.headers['location']
    assert _status(lid) == 'new'


def test_setting_the_same_status_writes_no_timeline_noise(db, client):
    lid = _lead('completed', '+14245556007')
    client.post(f'/leads/{lid}/status', data={'status': 'completed'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM activity
                            WHERE lead_id=%s AND kind='status'""", (lid,))
            assert cur.fetchone()['n'] == 0


def test_the_dropdown_offers_exactly_the_settable_statuses(db, client):
    """The screen and the route read the same list, so they cannot disagree."""
    import re
    lid = _lead('new', '+14245556008')
    body = client.get(f'/leads/{lid}').text
    form = re.search(r'action="[^"]*/status".*?</form>', body, re.S).group(0)
    offered = re.findall(r'<option value="(\w+)"', form)
    assert offered == list(web.MANUAL_STATUSES)
    assert 'dnc' not in offered and 'dialing' not in offered
