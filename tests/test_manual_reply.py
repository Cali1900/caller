"""
"I GOT A REPLY" — manual reply detection.

Sean reads every reply at this volume, so a person ticking a box IS the
detector. The important property is not the checkbox: it is that this writes
the SAME field every guard already reads, so when automatic ingest lands it
becomes a second writer rather than a replacement and nothing downstream
changes.
"""

import pytest
from fastapi.testclient import TestClient

from api import autosend, campaigns as c, db as dbm, dialer, stages


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _lead(status='completed', phone='+14245559000', emailed=True):
    cid = c.create('MR-' + phone[-4:])['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, stage, status, pool_status,
                               emailed_at, website)
                           VALUES (%s,'W','Bob','b@f.example',true,
                                   'America/Los_Angeles',%s,'L2',%s,'active',
                                   CASE WHEN %s THEN now() END,
                                   'https://f.example')
                           RETURNING lead_id""", (phone, cid, status, emailed))
            return cur.fetchone()['lead_id'], cid


def _lead_row(lid):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM leads WHERE lead_id=%s', (lid,))
            return dict(cur.fetchone())


def test_ticking_stamps_replied_at_and_sets_engaged(db, client):
    lid, _ = _lead(phone='+14245559001')
    r = client.post(f'/leads/{lid}/reply',
                    data={'got': '1', 'note': 'Interested, asked for pricing'},
                    follow_redirects=False)
    assert r.status_code == 303
    row = _lead_row(lid)
    assert row['replied_at'] is not None
    assert row['status'] == 'engaged'
    assert 'pricing' in row['reply_note']
    assert row['replied_by'] == 'operator'


def test_it_lands_on_the_timeline_as_a_HAND_action(db, client):
    lid, _ = _lead(phone='+14245559002')
    client.post(f'/leads/{lid}/reply', data={'got': '1', 'note': 'said no'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT summary, detail, created_at FROM activity
                            WHERE lead_id=%s AND kind='reply'""", (lid,))
            row = cur.fetchone()
    assert 'BY HAND' in row['summary']
    assert 'said no' in row['detail']
    assert row['created_at'] is not None


# ---------------------------------------------------------------------------
# THE GUARD — this is why it is built before the drip
# ---------------------------------------------------------------------------

def test_a_recorded_reply_blocks_auto_send(db, client):
    """
    The drip's auto-send must check this EXACTLY as it would check automatic
    detection. Four emails to somebody who already answered is the worst thing
    this system can do.
    """
    lid, cid = _lead(phone='+14245559003')
    c.update(cid, email_1_mode='auto')
    lead = _lead_row(lid)
    assert autosend.eligibility(lead, c.get(cid),
                                validate=lambda e, w=None: {
                                    'ok': True, 'domain_class': 'website',
                                    'reasons': []})['ok'] is True

    client.post(f'/leads/{lid}/reply', data={'got': '1'}, follow_redirects=False)
    d = autosend.eligibility(_lead_row(lid), c.get(cid),
                             validate=lambda e, w=None: {
                                 'ok': True, 'domain_class': 'website',
                                 'reasons': []})
    assert d['ok'] is False
    assert autosend.HoldReason.ALREADY_REPLIED in d['reasons']


def test_a_recorded_reply_removes_it_from_the_dialer(db, client, cfg_env,
                                                    monkeypatch):
    """
    REPLIED_GUARD in the selection query - the same field, one writer.

    The calling windows are neutralised here: they have their own dedicated
    tests, and leaving them in would make this fail for the time of day rather
    than for the reason under test.
    """
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    lid, cid = _lead(status='new', phone='+14245559004')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads SET stage='L1', next_attempt_at=now()
                            WHERE lead_id=%s""", (lid,))
    c.start(cid)
    sql = dialer._build_select()
    def selectable():
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {'limit': 50, 'campaign_id': cid})
                return {str(r['lead_id']) for r in cur.fetchall()}
    assert str(lid) in selectable()
    client.post(f'/leads/{lid}/reply', data={'got': '1'}, follow_redirects=False)
    assert str(lid) not in selectable()


# ---------------------------------------------------------------------------
# write-once, and an explicit undo
# ---------------------------------------------------------------------------

def test_ticking_twice_does_not_restamp(db, client):
    """
    The FIRST reply is when they answered. Moving that timestamp would rewrite
    every "N days after" measurement resting on it - the same reason
    emailed_at is write-once.
    """
    lid, _ = _lead(phone='+14245559005')
    client.post(f'/leads/{lid}/reply', data={'got': '1', 'note': 'first'},
                follow_redirects=False)
    first = _lead_row(lid)['replied_at']
    r = client.post(f'/leads/{lid}/reply', data={'got': '1', 'note': 'second'},
                    follow_redirects=False)
    assert 'already' in r.headers['location'].replace('%20', ' ').lower()
    row = _lead_row(lid)
    assert row['replied_at'] == first
    assert row['reply_note'] == 'first', 'the first account survives'


def test_unticking_clears_it_and_says_so_on_the_timeline(db, client):
    """
    A misclick must be undoable - but NOT silently. A lead whose reply quietly
    vanished is one somebody emails again without knowing why they should not.
    """
    lid, _ = _lead(phone='+14245559006')
    client.post(f'/leads/{lid}/reply', data={'got': '1'}, follow_redirects=False)
    client.post(f'/leads/{lid}/reply/undo', follow_redirects=False)
    row = _lead_row(lid)
    assert row['replied_at'] is None and row['reply_note'] is None
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM activity
                            WHERE lead_id=%s AND summary LIKE '%%WITHDRAWN%%'""",
                        (lid,))
            assert cur.fetchone()['n'] == 1


def test_unticking_does_not_guess_the_status_back(db, client):
    """
    `engaged` may have been reached by a click as well. Guessing which is
    wrong more often than leaving it, and the status dropdown is right there.
    """
    lid, _ = _lead(phone='+14245559007')
    client.post(f'/leads/{lid}/reply', data={'got': '1'}, follow_redirects=False)
    client.post(f'/leads/{lid}/reply/undo', follow_redirects=False)
    assert _lead_row(lid)['status'] == 'engaged'


def test_unticking_when_nothing_was_recorded_is_harmless(db, client):
    lid, _ = _lead(phone='+14245559008')
    r = client.post(f'/leads/{lid}/reply/undo', follow_redirects=False)
    assert 'No reply' in r.headers['location'].replace('%20', ' ')


def test_after_unticking_it_can_be_recorded_again(db, client):
    lid, _ = _lead(phone='+14245559009')
    client.post(f'/leads/{lid}/reply', data={'got': '1'}, follow_redirects=False)
    client.post(f'/leads/{lid}/reply/undo', follow_redirects=False)
    client.post(f'/leads/{lid}/reply', data={'got': '1', 'note': 'again'},
                follow_redirects=False)
    assert _lead_row(lid)['reply_note'] == 'again'


def test_automatic_ingest_would_be_a_SECOND_WRITER_not_a_replacement(db):
    """
    record_reply takes `by`, so an automatic source records itself as such and
    every guard keeps reading ONE field. This is the property that lets ingest
    land later without touching the dialer, the gate or the drip.
    """
    lid, _ = _lead(phone='+14245559010')
    assert stages.record_reply(lid, note='auto-detected', by='auto:imap') is True
    row = _lead_row(lid)
    assert row['replied_by'] == 'auto:imap'
    assert row['replied_at'] is not None
    assert row['status'] == 'engaged'
