"""
Suppression, enforced in BOTH places, plus the allowlist at dial time.

Break-pass targets (scripts/break_pass.sh):
  1. dialer.SUPPRESSION_JOIN   - removing it must turn a test red
  2. assert_not_suppressed     - removing the pre-dial re-check must too

They test different things and BOTH are needed. The join stops a suppressed
number ever being a candidate. The re-check catches the number that was
suppressed AFTER the batch was claimed - a call ending in "remove me" while an
earlier batch is still in flight.
"""

import pytest

from api import dialer
from api.config import load_config


@pytest.fixture
def cfg(db, monkeypatch):
    monkeypatch.setenv('DIAL_MODE', 'allowlist')
    monkeypatch.setenv('DIAL_ALLOWLIST', '+15551234567')
    # PHASE 2: the window is tested in test_windows.py with its own break
    # pass. Neutralised here so a suppression test fails for suppression
    # reasons and not because the suite happens to run at 3am.
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    return load_config()


@pytest.fixture(autouse=True)
def _queue_the_lead(db, request):
    """
    PHASE 5d: a lead is only a candidate if it is IN THE STANDING QUEUE and
    dialing is switched on. The switch defaults to OFF, so every test that
    expects a dial has to turn it on.
    """
    if 'lead' not in request.fixturenames:
        return
    from api import settings as settings_mod
    settings_mod._cache.update(at=0.0, values=None)
    # `lead` attaches the lead to a RUNNING campaign; that is the switch now.
    lead = request.getfixturevalue('lead')
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='active' WHERE lead_id=%s",
                    (lead['lead_id'],))
    db.commit()
    settings_mod._cache.update(at=0.0, values=None)


@pytest.fixture
def no_real_calls(monkeypatch):
    """Never touch Retell from a test."""
    placed = []

    class R:
        def __init__(self, n): self.call_id = f'call_fake_{n}'

    def fake(cfg, to_number, lead, dynamic=None):
        placed.append(to_number)
        return R(len(placed))
    monkeypatch.setattr('api.retell.create_phone_call', fake)
    return placed


def _suppress(db, phone, reason='requested'):
    with db.cursor() as cur:
        cur.execute(
            'INSERT INTO suppression (phone_e164, reason) VALUES (%s, %s)',
            (phone, reason))
    db.commit()


def _audits(db):
    with db.cursor() as cur:
        cur.execute('SELECT outcome, detail FROM dial_audit ORDER BY id')
        return cur.fetchall()


# --------------------------------------------------------------------------
# 1. joined into the SELECT - never a candidate
# --------------------------------------------------------------------------

def test_suppressed_number_is_never_a_candidate(db, lead, cfg):
    _suppress(db, lead['phone_e164'])
    assert dialer.select_and_claim(cfg) == []


def test_unsuppressed_number_is_a_candidate(db, lead, cfg):
    claimed = dialer.select_and_claim(cfg)
    assert [c['phone_e164'] for c in claimed] == [lead['phone_e164']]


def test_claiming_marks_the_lead_dialing(db, lead, cfg):
    dialer.select_and_claim(cfg)
    with db.cursor() as cur:
        cur.execute('SELECT status FROM leads WHERE lead_id=%s', (lead['lead_id'],))
        assert cur.fetchone()['status'] == 'dialing'


def test_a_claimed_lead_is_not_claimed_twice(db, lead, cfg):
    """Second worker, or a re-entrant cron, must not double-dial."""
    assert len(dialer.select_and_claim(cfg)) == 1
    assert dialer.select_and_claim(cfg) == []


# --------------------------------------------------------------------------
# 2. re-checked immediately before the dial
# --------------------------------------------------------------------------

def test_suppressed_after_claim_is_refused_before_dialing(db, lead, cfg, no_real_calls):
    """
    The race the join alone cannot catch: the batch was selected, THEN the
    number went on the list. Without the re-check this dials a DNC number.
    """
    claimed = dialer.select_and_claim(cfg)
    assert len(claimed) == 1

    _suppress(db, lead['phone_e164'])          # after the claim

    assert dialer.dial_one(cfg, claimed[0]) is None
    assert no_real_calls == []                 # nothing was dialed

    outcomes = [a['outcome'] for a in _audits(db)]
    assert 'refused_suppressed' in outcomes

    with db.cursor() as cur:
        cur.execute('SELECT status FROM leads WHERE lead_id=%s', (lead['lead_id'],))
        assert cur.fetchone()['status'] == 'new'      # returned to prior status


# --------------------------------------------------------------------------
# allowlist at dial time, and the happy path
# --------------------------------------------------------------------------

def test_off_allowlist_is_refused_and_audited(db, lead, cfg, monkeypatch, no_real_calls):
    monkeypatch.setenv('DIAL_MODE', 'allowlist')
    monkeypatch.setenv('DIAL_ALLOWLIST', '')      # empty: dials nothing
    empty_cfg = load_config()

    claimed = dialer.select_and_claim(empty_cfg)
    assert dialer.dial_one(empty_cfg, claimed[0]) is None
    assert no_real_calls == []
    assert 'refused_allowlist' in [a['outcome'] for a in _audits(db)]


def test_allowlisted_number_dials_and_is_audited(db, lead, cfg, no_real_calls):
    claimed = dialer.select_and_claim(cfg)
    call_id = dialer.dial_one(cfg, claimed[0])

    assert call_id == 'call_fake_1'
    assert no_real_calls == ['+15551234567']

    with db.cursor() as cur:
        cur.execute('SELECT last_call_id, attempts FROM leads WHERE lead_id=%s',
                    (lead['lead_id'],))
        row = cur.fetchone()
    assert row['last_call_id'] == 'call_fake_1'
    assert row['attempts'] == 1
    assert 'dialed' in [a['outcome'] for a in _audits(db)]


def test_every_refusal_is_audited_never_silent(db, lead, cfg, no_real_calls):
    """A silent refusal is how you spend an hour asking why nothing dialed."""
    _suppress(db, lead['phone_e164'])
    claimed = dialer.select_and_claim(cfg)           # join blocks it
    assert claimed == []
    # and the direct path is audited too
    fake_lead = {'lead_id': lead['lead_id'], 'phone_e164': lead['phone_e164'],
                 'prior_status': 'new'}
    assert dialer.dial_one(cfg, fake_lead) is None
    assert len(_audits(db)) >= 1
