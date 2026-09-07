"""
Campaigns, the cap, pause, carry-overs and rollover.

These are the phase-2 done-when conditions, expressed as tests.
"""

import datetime

import pytest

from api import campaigns, dialer, upload

LA = 'America/Los_Angeles'


@pytest.fixture
def no_real_calls(monkeypatch):
    calls = []

    class R:
        def __init__(self, n): self.call_id = f'call_fake_{n}'

    def fake(cfg, to_number, lead_id, dynamic_vars=None):
        calls.append(to_number)
        return R(len(calls))
    monkeypatch.setattr('api.retell.create_phone_call', fake)
    return calls


def _pool_leads(db, n, prefix='+1555100'):
    """n leads in the POOL (as CSV upload leaves them)."""
    ids = []
    with db.cursor() as cur:
        for i in range(n):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status)
                           VALUES (%s,%s,%s,'pool','new') RETURNING lead_id""",
                        (f'Firm {i}', f'{prefix}{i:04d}', LA))
            ids.append(cur.fetchone()['lead_id'])
    db.commit()
    return ids


def _allow_all(monkeypatch):
    """Window and allowlist are tested elsewhere; neutralise them here so a
    cap test fails for cap reasons only."""
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')


# --------------------------------------------------------------------------
# uploading never dials
# --------------------------------------------------------------------------

def test_upload_500_leads_places_zero_calls(db, cfg_dialable, no_real_calls, monkeypatch):
    _allow_all(monkeypatch)
    rows = ['company,phone,timezone']
    rows += [f'Firm {i},+1555200{i:04d},{LA}' for i in range(500)]
    report = upload.upload('\n'.join(rows))
    assert report['inserted'] == 500

    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM leads WHERE pool_status='pool'")
        assert cur.fetchone()['n'] == 500

    # even with a started campaign, nothing is enrolled, so nothing dials
    campaigns.ensure(cfg_dialable, daily_cap=200)
    campaigns.start(cfg_dialable)
    assert dialer.run_once(cfg_dialable, limit=50) == 0
    assert no_real_calls == []


def test_nothing_dials_until_start(db, cfg_dialable, no_real_calls, monkeypatch):
    _allow_all(monkeypatch)
    _pool_leads(db, 5)
    campaigns.ensure(cfg_dialable, daily_cap=200)
    campaigns.enroll(cfg_dialable)                    # enrolled, but NOT started
    assert dialer.run_once(cfg_dialable, limit=10) == 0
    assert no_real_calls == []

    campaigns.start(cfg_dialable)
    assert dialer.run_once(cfg_dialable, limit=10) == 5
    assert len(no_real_calls) == 5


def test_pause_stops_new_dials_immediately(db, cfg_dialable, no_real_calls, monkeypatch):
    _allow_all(monkeypatch)
    _pool_leads(db, 6)
    campaigns.ensure(cfg_dialable, daily_cap=200)
    campaigns.enroll(cfg_dialable)
    campaigns.start(cfg_dialable)

    date = campaigns.campaign_date(cfg_dialable)
    claimed = dialer.select_and_claim(cfg_dialable, date=date, limit=6)
    assert len(claimed) == 6

    campaigns.pause(cfg_dialable)                     # pressed AFTER the claim

    # every already-claimed lead is refused at the pre-dial check
    for lead in claimed:
        assert dialer.dial_one(cfg_dialable, lead) is None
    assert no_real_calls == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM dial_audit WHERE outcome='refused_paused'")
        assert cur.fetchone()['n'] == 6


# --------------------------------------------------------------------------
# the cap is a TOTAL
# --------------------------------------------------------------------------

def test_cap_counts_carryovers_not_just_fresh(db, cfg_env, monkeypatch):
    """cap 200 with 50 carry-overs => 150 fresh, not 200."""
    _allow_all(monkeypatch)
    with db.cursor() as cur:
        for i in range(50):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status, next_attempt_at)
                           VALUES (%s,%s,%s,'active','callback', now())""",
                        (f'CB {i}', f'+1555300{i:04d}', LA))
    db.commit()
    _pool_leads(db, 400, prefix='+1555400')

    counts = campaigns.enroll(cfg_env)
    assert counts['callback'] == 50
    assert counts['fresh'] == 150, counts
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM campaign_leads')
        assert cur.fetchone()['n'] == 200


def test_carryovers_exceeding_the_cap_dial_and_zero_fresh_are_added(db, cfg_env, monkeypatch):
    """Promised callbacks beat cold calls."""
    _allow_all(monkeypatch)
    campaigns.ensure(cfg_env, daily_cap=20)
    with db.cursor() as cur:
        for i in range(30):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status, next_attempt_at)
                           VALUES (%s,%s,%s,'active','callback', now())""",
                        (f'CB {i}', f'+1555500{i:04d}', LA))
    db.commit()
    _pool_leads(db, 50, prefix='+1555600')

    counts = campaigns.enroll(cfg_env)
    assert counts['callback'] == 30
    assert counts['fresh'] == 0, 'cold calls must not be added past the cap'


def test_hard_cap_refuses_fresh_at_dial_time(db, cfg_dialable, no_real_calls, monkeypatch):
    """
    The authoritative check. Enrolment can be re-run and a cap can be lowered
    mid-day; neither may overshoot.
    """
    _allow_all(monkeypatch)
    campaigns.ensure(cfg_dialable, daily_cap=3)
    _pool_leads(db, 6, prefix='+1555700')
    campaigns.enroll(cfg_dialable)
    campaigns.start(cfg_dialable)

    # force-enrol beyond the cap, as a re-run or a lowered cap would
    date = campaigns.campaign_date(cfg_dialable)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                       SELECT %s, lead_id, 'fresh' FROM leads
                        WHERE pool_status='pool'
                       ON CONFLICT DO NOTHING""", (date,))
        cur.execute("UPDATE leads SET pool_status='active' WHERE pool_status='pool'")
    db.commit()

    placed = dialer.run_once(cfg_dialable, limit=20)
    assert placed == 3, f'cap 3 but placed {placed}'
    assert len(no_real_calls) == 3
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM dial_audit WHERE outcome='refused_cap'")
        assert cur.fetchone()['n'] >= 1


def test_carryovers_still_dial_after_the_cap_is_hit(db, cfg_dialable, no_real_calls, monkeypatch):
    _allow_all(monkeypatch)
    campaigns.ensure(cfg_dialable, daily_cap=1)
    with db.cursor() as cur:
        for i in range(3):
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status, next_attempt_at)
                           VALUES (%s,%s,%s,'active','callback', now())""",
                        (f'CB {i}', f'+1555800{i:04d}', LA))
    db.commit()
    campaigns.enroll(cfg_dialable)
    campaigns.start(cfg_dialable)
    placed = dialer.run_once(cfg_dialable, limit=10)
    assert placed == 3, 'promises must dial even past the cap'


# --------------------------------------------------------------------------
# carry-overs auto-enrol, rollover
# --------------------------------------------------------------------------

def test_callbacks_auto_enrol_without_approval(db, cfg_env, monkeypatch):
    _allow_all(monkeypatch)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                          pool_status, status, next_attempt_at)
                       VALUES ('CB','+15559990001',%s,'active','callback', now())""",
                    (LA,))
    db.commit()
    counts = campaigns.enroll(cfg_env)
    assert counts['callback'] == 1


def test_rollover_carries_undialed_to_today_at_the_front(db, cfg_env, monkeypatch):
    _allow_all(monkeypatch)
    today = campaigns.campaign_date(cfg_env)
    y = today - datetime.timedelta(days=1)
    ids = _pool_leads(db, 3, prefix='+1555910')
    with db.cursor() as cur:
        cur.execute('INSERT INTO campaigns (campaign_date, daily_cap) VALUES (%s, 200)', (y,))
        for lid in ids:
            cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                           VALUES (%s,%s,'fresh')""", (y, lid))
    db.commit()

    r = campaigns.rollover(cfg_env, today)
    assert r['rolled_over'] == 3
    with db.cursor() as cur:
        cur.execute("""SELECT source FROM campaign_leads
                        WHERE campaign_date=%s AND lead_id=%s""", (today, ids[0]))
        assert cur.fetchone()['source'] == 'rollover'
        cur.execute('SELECT rollover_days FROM leads WHERE lead_id=%s', (ids[0],))
        assert cur.fetchone()['rollover_days'] == 1


def test_rollover_is_idempotent(db, cfg_env, monkeypatch):
    """Runs on every worker tick - it must not inflate the counter."""
    _allow_all(monkeypatch)
    today = campaigns.campaign_date(cfg_env)
    y = today - datetime.timedelta(days=1)
    ids = _pool_leads(db, 2, prefix='+1555920')
    with db.cursor() as cur:
        cur.execute('INSERT INTO campaigns (campaign_date, daily_cap) VALUES (%s,200)', (y,))
        for lid in ids:
            cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                           VALUES (%s,%s,'fresh')""", (y, lid))
    db.commit()
    for _ in range(4):
        campaigns.rollover(cfg_env, today)
    with db.cursor() as cur:
        cur.execute('SELECT rollover_days FROM leads WHERE lead_id=%s', (ids[0],))
        assert cur.fetchone()['rollover_days'] == 1


def test_five_day_rollover_is_flagged(db, cfg_env, monkeypatch):
    _allow_all(monkeypatch)
    today = campaigns.campaign_date(cfg_env)
    y = today - datetime.timedelta(days=1)
    ids = _pool_leads(db, 1, prefix='+1555930')
    with db.cursor() as cur:
        cur.execute('UPDATE leads SET rollover_days=4 WHERE lead_id=%s', (ids[0],))
        cur.execute('INSERT INTO campaigns (campaign_date, daily_cap) VALUES (%s,200)', (y,))
        cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                       VALUES (%s,%s,'fresh')""", (y, ids[0]))
    db.commit()
    r = campaigns.rollover(cfg_env, today)
    assert r['flagged'] == 1
    with db.cursor() as cur:
        cur.execute("""SELECT count(*) AS n FROM activity
                        WHERE lead_id=%s AND summary LIKE '%%undialed%%'""", (ids[0],))
        assert cur.fetchone()['n'] == 1


def test_suppressed_leads_are_never_enrolled(db, cfg_env, monkeypatch):
    _allow_all(monkeypatch)
    ids = _pool_leads(db, 1, prefix='+1555940')
    with db.cursor() as cur:
        cur.execute("SELECT phone_e164 FROM leads WHERE lead_id=%s", (ids[0],))
        phone = cur.fetchone()['phone_e164']
        cur.execute("INSERT INTO suppression (phone_e164, reason) VALUES (%s,'requested')",
                    (phone,))
    db.commit()
    counts = campaigns.enroll(cfg_env)
    assert counts['fresh'] == 0


# --------------------------------------------------------------------------
# the SQL gate, observed at SELECTION rather than at dial time
#
# assert_campaign_running() also blocks an unstarted or paused campaign, so
# run_once() returns 0 either way. But without the gate in the query the leads
# are still CLAIMED first - status flips to 'dialing', then gets reverted, and
# every one writes a dial_audit row. Selection must not touch them at all.
# --------------------------------------------------------------------------

def test_selection_ignores_an_unstarted_campaign(db, cfg_dialable, monkeypatch):
    _allow_all(monkeypatch)
    _pool_leads(db, 4, prefix='+1555950')
    campaigns.ensure(cfg_dialable, daily_cap=200)
    campaigns.enroll(cfg_dialable)                 # enrolled, never started

    assert dialer.select_and_claim(cfg_dialable, limit=10) == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM leads WHERE status='dialing'")
        assert cur.fetchone()['n'] == 0, 'unstarted campaign must not churn lead status'
        cur.execute('SELECT count(*) AS n FROM dial_audit')
        assert cur.fetchone()['n'] == 0, 'unstarted campaign must not fill dial_audit'


def test_selection_ignores_a_paused_campaign(db, cfg_dialable, monkeypatch):
    _allow_all(monkeypatch)
    _pool_leads(db, 4, prefix='+1555960')
    campaigns.ensure(cfg_dialable, daily_cap=200)
    campaigns.enroll(cfg_dialable)
    campaigns.start(cfg_dialable)
    campaigns.pause(cfg_dialable)

    assert dialer.select_and_claim(cfg_dialable, limit=10) == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM leads WHERE status='dialing'")
        assert cur.fetchone()['n'] == 0
