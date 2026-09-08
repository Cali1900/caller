"""
THE SENDER — the only thing that puts campaign mail on the wire.

NOTHING IN THIS FILE MAY SEND. mail.send is replaced in every test; a test that
forgets is caught by the autouse fixture, which makes the real one explode.
"""

import datetime

import pytest

from api import (autosend, campaigns as c, db as dbm, drafts, guards,
                 sender, stages)
from api.config import load_config


@pytest.fixture(autouse=True)
def never_really_send(monkeypatch):
    """
    A test that forgets to stub the transport must FAIL, not send.
    """
    def explode(*a, **k):
        raise AssertionError('a test tried to send real mail')
    monkeypatch.setattr('api.mail._post', explode, raising=False)
    monkeypatch.setattr('urllib.request.urlopen', explode)
    yield


@pytest.fixture
def outbox(monkeypatch):
    box = []
    def fake(cfg, to, subject, text, timeout=20, attachments=None,
             sender_email=None, sender_name=None):
        box.append({'to': to, 'subject': subject, 'body': text,
                    'sender_email': sender_email, 'sender_name': sender_name})
        return {'ok': True, 'detail': 'queued'}
    monkeypatch.setattr('api.mail.send', fake)
    return box


@pytest.fixture
def allow_all(monkeypatch):
    monkeypatch.setenv('EMAIL_MODE', 'unrestricted')
    return load_config()


def _ready_lead(email='bob@firm.com', minutes_ago=60, **campaign_kw):
    """A lead that SHOULD auto-send: auto campaign, delay elapsed, clean."""
    row = c.create('SND-' + str(abs(hash(email + str(campaign_kw))) % 9999))
    cid = row['campaign_id']
    c.update(cid, email_1_mode='auto', email_1_delay_minutes=15, **campaign_kw)
    when = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, stage, status, website,
                               last_called_at)
                           VALUES (%s,'Firm','Bob Smith',%s,true,
                                   'America/Los_Angeles',%s,'L2','completed',
                                   'https://firm.com',%s)
                           RETURNING lead_id""",
                        ('+1424555' + str(abs(hash(email)) % 9000 + 1000),
                         email, cid, when))
            lid = cur.fetchone()['lead_id']
    drafts.generate_for(lid)
    return lid, cid


CLEAN_DNS = lambda d: (True, None)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr('api.email_validation.has_mx', CLEAN_DNS)


# ---------------------------------------------------------------------------
# THE DEV GUARD — the same shape as DIAL_ALLOWLIST
# ---------------------------------------------------------------------------

def test_an_empty_allowlist_sends_nothing(db, outbox, monkeypatch):
    """A dev box with no list configured must not be able to mail anyone."""
    monkeypatch.setenv('EMAIL_MODE', 'allowlist')
    monkeypatch.setenv('EMAIL_ALLOWLIST', '')
    lid, _ = _ready_lead()
    r = sender.send_one(load_config(), lid)
    assert r['sent'] is False
    assert outbox == []


def test_an_address_on_the_allowlist_sends(db, outbox, monkeypatch):
    monkeypatch.setenv('EMAIL_MODE', 'allowlist')
    monkeypatch.setenv('EMAIL_ALLOWLIST', 'bob@firm.com')
    lid, _ = _ready_lead('bob@firm.com')
    assert sender.send_one(load_config(), lid)['sent'] is True
    assert [m['to'] for m in outbox] == ['bob@firm.com']


def test_the_allowlist_is_case_insensitive(db, outbox, monkeypatch):
    """An allowlist that misses because someone typed Bob@Firm.com is an
    allowlist that failed open in the only direction that matters."""
    monkeypatch.setenv('EMAIL_MODE', 'allowlist')
    monkeypatch.setenv('EMAIL_ALLOWLIST', 'BOB@FIRM.COM')
    lid, _ = _ready_lead('bob@firm.com')
    assert sender.send_one(load_config(), lid)['sent'] is True


@pytest.mark.parametrize('mode', ['', ' ', 'unrestrictd', 'off', 'ALLOWLIST'])
def test_an_unknown_mode_refuses(db, outbox, monkeypatch, mode):
    """Anything that is not exactly 'unrestricted' or 'allowlist' refuses."""
    monkeypatch.setenv('EMAIL_MODE', mode)
    monkeypatch.setenv('EMAIL_ALLOWLIST', 'bob@firm.com')
    lid, _ = _ready_lead()
    assert sender.send_one(load_config(), lid)['sent'] is False
    assert outbox == []


def test_every_refusal_is_audited(db, outbox, monkeypatch):
    """A silent refusal is how you spend an hour asking why nothing sent."""
    monkeypatch.setenv('EMAIL_MODE', 'allowlist')
    monkeypatch.setenv('EMAIL_ALLOWLIST', '')
    lid, _ = _ready_lead()
    sender.send_one(load_config(), lid)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT outcome, detail FROM email_audit
                            WHERE lead_id=%s""", (lid,))
            row = cur.fetchone()
            assert row['outcome'] == 'refused_allowlist'
            assert 'allowlist' in row['detail']


def test_a_send_is_audited_too(db, outbox, allow_all):
    lid, _ = _ready_lead()
    sender.send_one(allow_all, lid)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT outcome FROM email_audit WHERE lead_id=%s", (lid,))
            assert cur.fetchone()['outcome'] == 'sent'


# ---------------------------------------------------------------------------
# exactly once
# ---------------------------------------------------------------------------

def test_a_lead_is_never_sent_twice(db, outbox, allow_all):
    """
    The send is CLAIMED by stamping emailed_at before the API call, and
    mark_emailed is write-once. Losing one email to a crash beats sending a
    second one to a real person.
    """
    lid, _ = _ready_lead()
    assert sender.send_one(allow_all, lid)['sent'] is True
    assert sender.send_one(allow_all, lid)['sent'] is False
    assert len(outbox) == 1


def test_a_transport_failure_does_not_unstamp(db, monkeypatch, allow_all):
    """
    We do not know whether Brevo accepted it. Un-stamping would let the next
    tick send a second copy - so the stamp stays and a person is told.
    """
    monkeypatch.setattr('api.mail.send',
                        lambda *a, **k: {'ok': False, 'detail': 'brevo 500'})
    lid, _ = _ready_lead()
    assert sender.send_one(allow_all, lid)['sent'] is False
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT emailed_at FROM leads WHERE lead_id=%s', (lid,))
            assert cur.fetchone()['emailed_at'] is not None, 'stamp must stay'
            cur.execute("""SELECT count(*) AS n FROM activity
                            WHERE lead_id=%s AND summary LIKE '%%FAILED%%'""",
                        (lid,))
            assert cur.fetchone()['n'] == 1, 'a person must be told'


# ---------------------------------------------------------------------------
# the gate is re-checked AT SEND TIME
# ---------------------------------------------------------------------------

def test_a_reply_between_selection_and_send_stops_it(db, outbox, allow_all):
    """
    The same lesson as dial_one: the gap between deciding and doing is real.
    """
    lid, _ = _ready_lead()
    due = sender.due(allow_all)
    assert lid in due
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE leads SET replied_at = now() WHERE lead_id=%s', (lid,))
    assert sender.send_one(allow_all, lid)['sent'] is False
    assert outbox == []


def test_switching_the_campaign_to_manual_stops_it(db, outbox, allow_all):
    lid, cid = _ready_lead()
    c.update(cid, email_1_mode='manual')
    assert sender.send_one(allow_all, lid)['sent'] is False
    assert outbox == []


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

def test_the_delay_is_respected(db, allow_all):
    lid, _ = _ready_lead(minutes_ago=5)      # delay is 15
    assert lid not in sender.due(allow_all)


def test_a_manual_campaign_produces_no_candidates(db, allow_all):
    lid, cid = _ready_lead()
    c.update(cid, email_1_mode='manual')
    assert lid not in sender.due(allow_all)


def test_it_sends_as_the_campaigns_verified_sender(db, outbox, allow_all):
    """Defaulting to the digest identity would send firm mail from the wrong
    address."""
    lid, cid = _ready_lead()
    c.update(cid, sender_email='sean@demandcounselor.com', sender_name='Sean')
    sender.send_one(allow_all, lid)
    assert outbox[0]['sender_email'] == 'sean@demandcounselor.com'
    assert outbox[0]['sender_name'] == 'Sean'
