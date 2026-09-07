"""
The STANDING QUEUE.

One ongoing queue, no per-day ritual. The property that matters most:
removing the start button removed what stopped "add 500 leads" becoming
"dial 500 now", so it is replaced by ONE explicit switch that DEFAULTS TO OFF.
"""

import pytest

from api import dialer, settings as settings_mod, upload

LA = 'America/Los_Angeles'


@pytest.fixture(autouse=True)
def _no_window(monkeypatch):
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')


@pytest.fixture
def no_real_calls(monkeypatch):
    placed = []

    class R:
        def __init__(self, n): self.call_id = f'call_fake_{n}'

    def fake(cfg, to_number, lead, dynamic=None):
        placed.append(to_number)
        return R(len(placed))
    monkeypatch.setattr('api.retell.create_phone_call', fake)
    monkeypatch.setenv('DIAL_MODE', 'unrestricted')
    return placed


def _pool_leads(db, n, prefix='+1555100', **kw):
    ids = []
    cols = {'pool_status': 'pool', 'status': 'new'}
    cols.update(kw)
    with db.cursor() as cur:
        for i in range(n):
            cur.execute(
                f"""INSERT INTO leads (company, phone_e164, timezone,
                                       pool_status, status
                                       {''.join(',' + k for k in kw if k not in ('pool_status','status'))})
                    VALUES (%s,%s,%s,%s,%s
                            {''.join(',%s' for k in kw if k not in ('pool_status','status'))})
                    RETURNING lead_id""",
                [f'Firm {i}', f'{prefix}{i:04d}', LA, cols['pool_status'], cols['status']]
                + [kw[k] for k in kw if k not in ('pool_status', 'status')])
            ids.append(cur.fetchone()['lead_id'])
    db.commit()
    return ids


def _cfg():
    from api.config import load_config
    return load_config()


# --------------------------------------------------------------------------
# THE SWITCH - defaults off, and adding leads can never turn it on
# --------------------------------------------------------------------------

def test_dialing_defaults_to_off(db):
    settings_mod._cache.update(at=0.0, values=None)
    assert settings_mod.all_settings(force=True)['dialing_enabled'] is False


def test_queueing_five_hundred_leads_places_zero_calls(db, no_real_calls):
    """
    THE POINT OF THE SWITCH. The old guard was the ABSENCE of a start button;
    a guard by omission disappears with the ritual it depended on.
    """
    rows = ['company,phone,timezone'] + [f'Firm {i},+1555200{i:04d},{LA}'
                                         for i in range(500)]
    assert upload.upload('\n'.join(rows))['inserted'] == 500
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='active'")   # add ALL to the queue
    db.commit()
    settings_mod._cache.update(at=0.0, values=None)

    assert dialer.run_once(_cfg(), limit=50) == 0
    assert no_real_calls == []


def test_turning_the_switch_on_starts_dialing(db, queued, no_real_calls):
    ids = _pool_leads(db, 3, prefix='+1555210')
    queued(ids)                       # queues AND switches dialing on
    # no explicit limit: run_once must honour max_concurrent, which is 1
    assert dialer.run_once(_cfg()) == 1
    assert len(no_real_calls) == 1


def test_pausing_stops_selection_immediately(db, queued, no_real_calls):
    ids = _pool_leads(db, 3, prefix='+1555220')
    queued(ids)
    assert len(dialer.select_and_claim(_cfg(), limit=10)) > 0

    settings_mod.set_many({'dialing_enabled': 'false'})
    assert dialer.select_and_claim(_cfg(), limit=10) == [], \
        'a paused queue must produce no candidates at all'


def test_a_lead_not_in_the_queue_is_never_a_candidate(db, queued):
    ids = _pool_leads(db, 2, prefix='+1555230')
    queued([])                        # switch on, but queue nothing
    assert dialer.select_and_claim(_cfg(), limit=10) == []


# --------------------------------------------------------------------------
# the daily cap counts NEW leads only
# --------------------------------------------------------------------------

def test_the_cap_counts_new_leads(db, queued, no_real_calls):
    settings_mod.set_many({'daily_cap': 2, 'max_concurrent': 1})
    queued(_pool_leads(db, 5, prefix='+1555240'))
    for _ in range(5):
        dialer.run_once(_cfg())
    assert len(no_real_calls) == 2, 'cap 2 means two NEW leads today'


def test_a_carryover_is_exempt_from_the_cap(db, queued, no_real_calls):
    """A callback is a promise already made; it must not wait for tomorrow."""
    settings_mod.set_many({'daily_cap': 1, 'max_concurrent': 1})
    import datetime
    ids = _pool_leads(db, 3, prefix='+1555250')
    with db.cursor() as cur:
        # two of them are already-started leads due again now
        cur.execute("""UPDATE leads SET status='callback', first_dialed_at=%s
                        WHERE lead_id = ANY(%s::uuid[])""",
                    (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
                     [str(i) for i in ids[:2]]))
    db.commit()
    queued(ids)
    for _ in range(4):
        dialer.run_once(_cfg())
    assert len(no_real_calls) == 3, 'two carry-overs plus one new lead under a cap of 1'


def test_carryovers_are_dialed_before_new_leads(db, queued, no_real_calls):
    import datetime
    settings_mod.set_many({'daily_cap': 100, 'max_concurrent': 1})
    new_ids = _pool_leads(db, 2, prefix='+1555260')
    old_ids = _pool_leads(db, 2, prefix='+1555270')
    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET status='callback', first_dialed_at=%s
                        WHERE lead_id = ANY(%s::uuid[])""",
                    (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
                     [str(i) for i in old_ids]))
        cur.execute("SELECT phone_e164 FROM leads WHERE lead_id = ANY(%s::uuid[])",
                    ([str(i) for i in old_ids],))
        carry_phones = {r['phone_e164'] for r in cur.fetchall()}
    db.commit()
    queued(new_ids + old_ids)
    dialer.run_once(_cfg()); dialer.run_once(_cfg())
    assert set(no_real_calls) == carry_phones, 'carry-overs go first'


def test_first_dialed_at_is_stamped_once(db, queued, no_real_calls):
    """A retry must never consume new-lead budget twice."""
    ids = _pool_leads(db, 1, prefix='+1555280')
    queued(ids)
    dialer.run_once(_cfg())
    with db.cursor() as cur:
        cur.execute('SELECT first_dialed_at FROM leads WHERE lead_id=%s', (ids[0],))
        first = cur.fetchone()['first_dialed_at']
        cur.execute("UPDATE leads SET status='no_answer', next_attempt_at=now() "
                    "WHERE lead_id=%s", (ids[0],))
    db.commit()
    dialer.run_once(_cfg())
    with db.cursor() as cur:
        cur.execute('SELECT first_dialed_at FROM leads WHERE lead_id=%s', (ids[0],))
        assert cur.fetchone()['first_dialed_at'] == first


# --------------------------------------------------------------------------
# uploading still never dials
# --------------------------------------------------------------------------

def test_upload_lands_in_the_pool_not_the_queue(db):
    upload.upload(f'company,phone,timezone\nA Firm,+14245559999,{LA}')
    with db.cursor() as cur:
        cur.execute('SELECT pool_status FROM leads')
        assert cur.fetchone()['pool_status'] == 'pool'


def test_nothing_is_lost_when_the_day_ends(db, queued, no_real_calls):
    """Anything not reached stays queued - there is no rollover step to miss."""
    settings_mod.set_many({'daily_cap': 1, 'max_concurrent': 1})
    ids = _pool_leads(db, 4, prefix='+1555290')
    queued(ids)
    for _ in range(4):
        dialer.run_once(_cfg())
    with db.cursor() as cur:
        cur.execute("""SELECT count(*) AS n FROM leads
                        WHERE pool_status='active' AND first_dialed_at IS NULL""")
        assert cur.fetchone()['n'] == 3, 'the undialed leads stay in the queue'
