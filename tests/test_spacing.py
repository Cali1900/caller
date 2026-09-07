"""
Dial spacing and operator-editable settings.

The property that matters: a call goes out at most every
[dial_interval_min, dial_interval_max] seconds, ONE at a time, and NOTHING
about the previous call's outcome can pull the next dial forward. Three busy
signals in a row must not fire three calls.
"""

import pytest

from api import campaigns, dialer, settings as settings_mod

LA = 'America/Los_Angeles'


@pytest.fixture(autouse=True)
def fresh_settings(db):
    settings_mod._cache.update(at=0.0, values=None)
    yield
    settings_mod._cache.update(at=0.0, values=None)


def _leads(db, n, prefix='+1555660'):
    ids = []
    with db.cursor() as cur:
        for i in range(n):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status)
                           VALUES (%s,%s,%s,'active','new') RETURNING lead_id""",
                        (f'Firm {i}', f'{prefix}{i:04d}', LA))
            ids.append(cur.fetchone()['lead_id'])
    db.commit()
    return ids


@pytest.fixture
def running(db, cfg_env, monkeypatch):
    """Standing queue: put leads in it and switch dialing on."""
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    from api import settings as settings_mod
    settings_mod._cache.update(at=0.0, values=None)
    settings_mod.set_many({'dialing_enabled': 'true', 'daily_cap': 1000},
                          updated_by='test')

    def _queue(ids):
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        with db.cursor() as cur:
            cur.execute("UPDATE leads SET pool_status='active' "
                        "WHERE lead_id = ANY(%s::uuid[])", ([str(i) for i in ids],))
        db.commit()
        settings_mod._cache.update(at=0.0, values=None)
    return _queue


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


# --------------------------------------------------------------------------
# one call per tick
# --------------------------------------------------------------------------

def test_a_tick_dials_only_max_concurrent_leads(db, running, no_real_calls, monkeypatch):
    """
    The real spacing control. With a batch limit of 10, a single tick could
    place ten calls 0.2s apart and defeat any interval however wide.
    """
    settings_mod.set_many({'max_concurrent': 1})
    running(_leads(db, 6))
    from api.config import load_config
    assert dialer.run_once(load_config()) == 1
    assert len(no_real_calls) == 1


def test_raising_max_concurrent_takes_effect_without_a_restart(db, running, no_real_calls):
    settings_mod.set_many({'max_concurrent': 3})
    running(_leads(db, 6, prefix='+1555661'))
    from api.config import load_config
    assert dialer.run_once(load_config()) == 3
    assert len(no_real_calls) == 3


# --------------------------------------------------------------------------
# the interval is TIME based, never outcome driven
# --------------------------------------------------------------------------

def test_the_gap_is_rerolled_within_the_configured_range():
    settings_mod.set_many({'dial_interval_min': 210, 'dial_interval_max': 300})
    import random
    s = settings_mod.all_settings(force=True)
    gaps = [random.uniform(s['dial_interval_min'], s['dial_interval_max'])
            for _ in range(200)]
    assert all(210 <= g <= 300 for g in gaps)
    assert len(set(round(g) for g in gaps)) > 20, 'a fixed cadence is itself a pattern'


def test_the_worker_rearms_the_gap_before_dialing_not_after(db):
    """
    Re-arming BEFORE the dial means the next call is a fixed wall-clock wait
    from this one, regardless of how long the call runs or how it ends. A
    busy signal cannot pull the next dial forward.
    """
    import inspect
    from api import worker
    src = inspect.getsource(worker.main)
    body = src[src.index('if now - last_dial >= dial_gap:'):]
    assign = body.index('last_dial = now')
    dial = body.index('dialer.run_once')
    assert assign < dial, 'last_dial must be re-armed BEFORE run_once()'


def test_nothing_in_the_dialer_shortens_the_gap_on_a_busy(db):
    """No outcome branch may write to the worker's timing."""
    import inspect
    from api import dialer as d
    src = inspect.getsource(d)
    for token in ('last_dial', 'dial_gap', 'DIAL_EVERY'):
        assert token not in src, f'{token} must not be reachable from the dialer'


# --------------------------------------------------------------------------
# busy is 15 minutes, not the generic ladder
# --------------------------------------------------------------------------

def test_busy_backs_off_15_minutes_not_the_generic_ladder(db):
    from api import drain
    assert '15 minutes' in drain.BACKOFF['busy']
    assert drain.REASON_MAP['dial_busy'] == 'busy'
    # and it is genuinely shorter than the no-answer rung
    assert '2 hours' in drain.BACKOFF['no_answer']


def test_three_busies_do_not_produce_three_immediate_dials(db, running, no_real_calls):
    """
    The failure this prevents. Each tick dials one; the LEAD that was busy is
    pushed 15 minutes out, so the next tick picks a DIFFERENT lead - it never
    re-dials the busy number immediately.
    """
    settings_mod.set_many({'max_concurrent': 1})
    ids = _leads(db, 3, prefix='+1555662')
    running(ids)
    from api.config import load_config
    cfg = load_config()

    for _ in range(3):
        dialer.run_once(cfg)
    assert len(no_real_calls) == 3
    assert len(set(no_real_calls)) == 3, 'each tick must take a different lead'


# --------------------------------------------------------------------------
# settings are operator-editable and validated
# --------------------------------------------------------------------------

def test_a_db_value_overrides_the_default(db):
    assert settings_mod.set_many({'dial_interval_min': 240})['ok']
    assert settings_mod.get('dial_interval_min') == 240


def test_min_greater_than_max_is_refused(db):
    settings_mod.set_many({'dial_interval_min': 210, 'dial_interval_max': 300})
    r = settings_mod.set_many({'dial_interval_min': 600})
    assert r['ok'] is False
    assert 'dial_interval_min' in r['errors']
    assert settings_mod.get('dial_interval_min') == 210, 'a refused write must change nothing'


@pytest.mark.parametrize('key,bad', [('max_concurrent', 0), ('max_concurrent', 99),
                                     ('dial_interval_min', 1), ('dial_interval_max', 99999)])
def test_out_of_range_is_refused_not_clamped(db, key, bad):
    """A typo that halves the spacing should be visible, not silently fixed."""
    r = settings_mod.set_many({key: bad})
    assert r['ok'] is False


def test_a_settings_outage_falls_back_slower_not_faster(db, monkeypatch):
    """If the table cannot be read, dialing must not speed up."""
    def boom():
        raise RuntimeError('db down')
    monkeypatch.setattr('api.db.get_conn', boom)
    settings_mod._cache.update(at=0.0, values=None)
    s = settings_mod.all_settings(force=True)
    assert s['max_concurrent'] == 1
    assert s['dial_interval_min'] >= 210
