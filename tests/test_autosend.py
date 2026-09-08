"""
THE AUTO-SEND GATE.

This is the first thing that mails without a person, so almost every test here
is about REFUSING. Each exclusion has a break definition; a silently-stopped
exclusion is an email to the wrong person, not a red test.

Nothing in this file can send mail: the gate returns a decision, and the sender
is a separate caller.
"""

import datetime

import pytest

from api import autosend, campaigns as c, db as dbm

CLEAN = lambda email, website=None: {'ok': True, 'domain_class': 'website',
                                     'reasons': [], 'domain': 'firm.com'}
BAD_DOMAIN = lambda email, website=None: {
    'ok': False, 'domain_class': 'neither', 'domain': 'x.biz',
    'reasons': ["x.biz is neither the firm's website domain nor known free-mail"]}


def _auto_campaign(**kw):
    row = c.create('AS-' + str(abs(hash(str(kw))) % 9999))
    return c.update(row['campaign_id'], email_1_mode='auto', **kw)


def _lead(**kw):
    base = {'dm_name': 'Bob Smith', 'dm_email': 'bob@firm.com',
            'dm_email_confirmed': True, 'replied_at': None,
            'status': 'completed', 'website': 'https://firm.com'}
    base.update(kw)
    return base


def test_a_clean_lead_on_an_auto_campaign_is_eligible(db):
    d = autosend.eligibility(_lead(), _auto_campaign(), validate=CLEAN)
    assert d['ok'] is True, d['reasons']


# ---------------------------------------------------------------------------
# the five exclusions
# ---------------------------------------------------------------------------

def test_1_an_unconfirmed_email_is_held(db):
    d = autosend.eligibility(_lead(dm_email_confirmed=False), _auto_campaign(),
                             validate=CLEAN)
    assert d['ok'] is False
    assert autosend.HoldReason.UNCONFIRMED in d['reasons']


@pytest.mark.parametrize('name', [None, '', '   '])
def test_2_no_contact_name_is_held(db, name):
    d = autosend.eligibility(_lead(dm_name=name), _auto_campaign(), validate=CLEAN)
    assert d['ok'] is False
    assert autosend.HoldReason.NO_NAME in d['reasons']


def test_3_a_domain_that_is_neither_site_nor_freemail_is_held(db):
    d = autosend.eligibility(_lead(dm_email='bob@x.biz'), _auto_campaign(),
                             validate=BAD_DOMAIN)
    assert d['ok'] is False
    assert any('neither' in r for r in d['reasons'])


def test_4_needs_human_is_held(db):
    d = autosend.eligibility(_lead(status='human_review'), _auto_campaign(),
                             validate=CLEAN)
    assert d['ok'] is False
    assert autosend.HoldReason.NEEDS_HUMAN in d['reasons']


def test_5_a_lead_that_already_replied_is_held(db):
    """
    Not on Sean's list - mine. A lead can carry replied_at from earlier work,
    and "they already answered us" is exactly what auto-send must not walk into.
    """
    d = autosend.eligibility(_lead(replied_at=datetime.datetime.now(datetime.UTC)),
                             _auto_campaign(), validate=CLEAN)
    assert d['ok'] is False
    assert autosend.HoldReason.ALREADY_REPLIED in d['reasons']


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------

def test_the_switch_defaults_to_manual(db):
    """Same rule as the dialer: turning a campaign on never starts sending."""
    row = c.create('AS-default')
    assert row['email_1_mode'] == 'manual'
    assert autosend.eligibility(_lead(), row, validate=CLEAN)['ok'] is False


def test_a_manual_campaign_never_auto_sends_however_clean_the_lead(db):
    row = c.create('AS-manual')
    d = autosend.eligibility(_lead(), row, validate=CLEAN)
    assert d['ok'] is False
    assert autosend.HoldReason.NOT_AUTO in d['reasons']


def test_a_lead_with_no_campaign_is_held(db):
    d = autosend.eligibility(_lead(), None, validate=CLEAN)
    assert d['ok'] is False
    assert autosend.HoldReason.NO_CAMPAIGN in d['reasons']


# ---------------------------------------------------------------------------
# FAIL CLOSED
# ---------------------------------------------------------------------------

def test_an_exception_in_the_check_holds_rather_than_sends(db):
    """
    THE PROPERTY THAT MATTERS MOST. An unexpected error is not permission to
    send. If the gate cannot decide, the answer is no.
    """
    def boom(email, website=None):
        raise RuntimeError('validation exploded')
    d = autosend.eligibility(_lead(), _auto_campaign(), validate=boom)
    assert d['ok'] is False
    assert any('could not run' in r for r in d['reasons'])


def test_a_dns_outage_holds_rather_than_sends(db):
    """The real path: email_validation already fails closed, and the gate
    inherits that instead of second-guessing it."""
    from api import email_validation as ev
    down = lambda d: (None, 'MX lookup failed (Timeout)')
    d = autosend.eligibility(
        _lead(), _auto_campaign(),
        validate=lambda e, w=None: ev.check(e, w, mx=down))
    assert d['ok'] is False


def test_every_failing_reason_is_reported_not_just_the_first(db):
    """Sean fixes what he can see. Reporting one problem at a time means three
    round trips for a lead with three problems."""
    d = autosend.eligibility(
        _lead(dm_name='', dm_email_confirmed=False, status='human_review'),
        _auto_campaign(), validate=CLEAN)
    assert len(d['reasons']) >= 3


# ---------------------------------------------------------------------------
# the hold reason is visible ON THE LEAD
# ---------------------------------------------------------------------------

def test_the_hold_reason_is_stored_on_the_lead(db):
    cid = _auto_campaign()['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, timezone,
                                              campaign_id)
                           VALUES ('+14245557001','W','America/Los_Angeles',%s)
                           RETURNING lead_id""", (cid,))
            lid = cur.fetchone()['lead_id']
    autosend.record(lid, {'ok': False, 'reasons': ['no contact name captured']})
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT autosend_hold_reason, autosend_checked_at
                             FROM leads WHERE lead_id=%s""", (lid,))
            row = cur.fetchone()
            assert 'no contact name' in row['autosend_hold_reason']
            assert row['autosend_checked_at'] is not None


def test_an_eligible_lead_clears_a_previous_hold(db):
    cid = _auto_campaign()['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, timezone,
                                              campaign_id)
                           VALUES ('+14245557002','W','America/Los_Angeles',%s)
                           RETURNING lead_id""", (cid,))
            lid = cur.fetchone()['lead_id']
    autosend.record(lid, {'ok': False, 'reasons': ['no contact name captured']})
    autosend.record(lid, {'ok': True, 'reasons': []})
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT autosend_hold_reason FROM leads WHERE lead_id=%s',
                        (lid,))
            assert cur.fetchone()['autosend_hold_reason'] is None


def test_nothing_in_this_module_sends(db):
    """The gate returns a DECISION. The sender is a separate caller, so no test
    of the gate can ever put mail on the wire."""
    import inspect
    src = inspect.getsource(autosend).lower()
    for token in ('smtp', 'brevo', 'mail.send', 'sendgrid', 'requests.post',
                  'urlopen'):
        assert token not in src, f'{token} must not appear in autosend.py'


def test_updating_an_unknown_campaign_field_is_refused_not_ignored(db):
    """
    REGRESSION. update() filtered silently to CONFIG_FIELDS, so adding
    email_1_mode as a column and forgetting to list it meant the call
    succeeded, returned a row, and changed nothing - the switch would have read
    as ON in the UI while the gate saw 'manual'.

    A write that goes nowhere and says nothing is the same family as the dead
    settings keys that made deploy.sh lie about pausing.
    """
    cid = c.create('AS-unknown')['campaign_id']
    with pytest.raises(ValueError) as e:
        c.update(cid, no_such_field='x')
    assert 'no_such_field' in str(e.value)


def test_the_switch_actually_persists(db):
    """The other half: a field that IS known must really change."""
    cid = c.create('AS-persist')['campaign_id']
    assert c.get(cid)['email_1_mode'] == 'manual'
    c.update(cid, email_1_mode='auto', email_1_delay_minutes=30)
    row = c.get(cid)
    assert row['email_1_mode'] == 'auto'
    assert row['email_1_delay_minutes'] == 30
