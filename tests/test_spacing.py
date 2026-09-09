"""
Dial spacing and operator-editable settings.

The property that matters: a call goes out at most every
[dial_interval_min, dial_interval_max] seconds, ONE at a time, and NOTHING
about the previous call's outcome can pull the next dial forward. Three busy
signals in a row must not fire three calls.
"""

import pytest

from api import campaigns, dialer

from conftest import running_campaign_id

LA = 'America/Los_Angeles'


@pytest.fixture(autouse=True)
def fresh_settings(db):
    yield


def _leads(db, n, prefix='+1555660'):
    ids = []
    with db.cursor() as cur:
        for i in range(n):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status, campaign_id)
                           VALUES (%s,%s,%s,'active','new',%s) RETURNING lead_id""",
                        (f'Firm {i}', f'{prefix}{i:04d}', LA, running_campaign_id()))
            ids.append(cur.fetchone()['lead_id'])
    db.commit()
    return ids


@pytest.fixture
def running(db, cfg_env, monkeypatch):
    """Standing queue: put leads in it and switch dialing on."""
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    from api import campaigns as c
    cid = running_campaign_id()          # the cap and the pause live HERE now
    c.update(cid, daily_cap=1000)

    def _queue(ids):
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        with db.cursor() as cur:
            cur.execute("UPDATE leads SET pool_status='active', campaign_id=%s "
                        "WHERE lead_id = ANY(%s::uuid[])",
                        (cid, [str(i) for i in ids],))
        db.commit()
    _queue.campaign_id = cid
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
    from api import campaigns as c
    # Set it ON THE CAMPAIGN, which is where the dialer reads it. This line
    # used to write settings['max_concurrent'], which nothing has consumed
    # since the named-campaign change; the assertion held only because the
    # campaign default is also 1, so the test did not control the value it
    # claims to. Set it to 2 and dial 6 so the number under test is not the
    # default - a test that passes when its own knob is ignored is not a test.
    c.update(running.campaign_id, max_concurrent=2)
    running(_leads(db, 6))
    from api.config import load_config
    assert dialer.run_once(load_config()) == 2
    assert len(no_real_calls) == 2


def test_raising_max_concurrent_takes_effect_without_a_restart(db, running, no_real_calls):
    from api import campaigns as c
    c.update(running.campaign_id, max_concurrent=3)   # config, not a redeploy
    running(_leads(db, 6, prefix='+1555661'))
    from api.config import load_config
    assert dialer.run_once(load_config()) == 3
    assert len(no_real_calls) == 3


# --------------------------------------------------------------------------
# the interval is TIME based, never outcome driven
# --------------------------------------------------------------------------

def test_the_gap_is_rerolled_within_the_configured_range(db):
    """
    Calls THE REAL PICKER. This test used to set the range in settings and then
    roll its own random.uniform over it - so it asserted on a range production
    stopped reading, using arithmetic production does not run. It passed either
    way. Spacing lives on the campaign, and worker.next_gap() is what the loop
    actually calls.
    """
    from api import campaigns as c, worker
    cid = running_campaign_id()
    c.update(cid, dial_interval_min=240, dial_interval_max=280)
    gaps = [worker.next_gap() for _ in range(200)]
    assert all(240 <= g <= 280 for g in gaps), f'out of range: {min(gaps)}..{max(gaps)}'
    assert len(set(round(g) for g in gaps)) > 20, 'a fixed cadence is itself a pattern'


def test_the_gap_falls_back_when_no_campaign_runs(db):
    """No campaign running means nothing dials, so the fallback only paces an
    idle loop - but it must still be a sane number, not a crash."""
    from api import campaigns as c, worker
    for row in c.list_all():
        c.stop(row['campaign_id'])
    assert 210 <= worker.next_gap() <= 300


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
    # BACKOFF is gone: the ladders live on the campaign now, so the property
    # is asserted where it is configured rather than against a module dict.
    # Busy is still the shortest, and for the same reason - a busy signal
    # means a human is there, the best signal in that list.
    from api import drain, retry_ladder as rl
    assert rl.minutes(rl.DEFAULTS['busy'][0]) == 15
    assert rl.minutes(rl.DEFAULTS['busy'][0]) < rl.minutes(rl.DEFAULTS['no_answer'][0])
    assert drain.REASON_MAP['dial_busy'] == 'busy'
    # and it is genuinely shorter than the no-answer rung
    assert rl.minutes(rl.DEFAULTS['no_answer'][0]) == 120


def test_three_busies_do_not_produce_three_immediate_dials(db, running, no_real_calls):
    """
    The failure this prevents. Each tick dials one; the LEAD that was busy is
    pushed 15 minutes out, so the next tick picks a DIFFERENT lead - it never
    re-dials the busy number immediately.
    """
    from api import campaigns as c
    c.update(running.campaign_id, max_concurrent=1)   # on the campaign, not settings
    ids = _leads(db, 3, prefix='+1555662')
    running(ids)
    from api.config import load_config
    cfg = load_config()

    for _ in range(3):
        dialer.run_once(cfg)
    assert len(no_real_calls) == 3
    assert len(set(no_real_calls)) == 3, 'each tick must take a different lead'


# --------------------------------------------------------------------------
# spacing is operator-editable and VALIDATED
#
# These used to exercise api/settings.py. That module is deleted - every piece
# of operator config belongs to a campaign now - so the same properties are
# asserted where they actually live. The range checks are enforced by DB CHECK
# constraints on campaign_configs, which is stronger than the Python
# validation was: nothing can write an out-of-range value, not even a script.
# --------------------------------------------------------------------------

def test_a_saved_value_is_what_the_dialer_reads(db):
    from api import campaigns as c, worker
    cid = running_campaign_id()
    c.update(cid, dial_interval_min=240, dial_interval_max=240)
    assert worker.next_gap() == 240


def test_min_greater_than_max_is_refused(db):
    """A refused write must change nothing."""
    import psycopg2
    from api import campaigns as c
    cid = running_campaign_id()
    c.update(cid, dial_interval_min=210, dial_interval_max=300)
    with pytest.raises(psycopg2.errors.CheckViolation):
        c.update(cid, dial_interval_min=600)
    assert c.get(cid)['dial_interval_min'] == 210


@pytest.mark.parametrize('field,bad', [('max_concurrent', 0), ('max_concurrent', 99),
                                       ('dial_interval_min', 1),
                                       ('dial_interval_max', 99999),
                                       ('daily_cap', 0), ('daily_cap', 99999)])
def test_out_of_range_is_refused_not_clamped(db, field, bad):
    """A typo that halves the spacing should be visible, not silently fixed."""
    import psycopg2
    from api import campaigns as c
    cid = running_campaign_id()
    before = c.get(cid)[field]
    with pytest.raises(psycopg2.errors.CheckViolation):
        c.update(cid, **{field: bad})
    assert c.get(cid)[field] == before


def test_an_outage_falls_back_SLOWER_not_faster(db, monkeypatch):
    """
    If the campaign cannot be read, dialing must not speed up.

    An outage that tightened the gap would dial a hundred firms in the time
    meant for twenty, and it would do it exactly when nobody is watching the
    database. The unknown case takes the WIDE default.
    """
    from api import worker
    def boom():
        raise RuntimeError('db down')
    monkeypatch.setattr('api.campaigns.running', boom)
    gaps = [worker.next_gap() for _ in range(50)]
    assert min(gaps) >= worker.FALLBACK_GAP[0] >= 210
    assert max(gaps) <= worker.FALLBACK_GAP[1]
