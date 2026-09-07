"""
The calling window.

These tests run the REAL selection query, not a copy. Expectations are derived
from the actual current time in each zone, so the suite is correct whenever it
runs - a test that only passes between 9 and 5 is worse than no test.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest

from api import campaigns, dialer

ZONES = ['America/New_York', 'America/Chicago', 'America/Denver',
         'America/Los_Angeles', 'Pacific/Honolulu', 'Europe/London',
         'Asia/Tokyo', 'Australia/Sydney']


def _local(tz):
    return datetime.datetime.now(ZoneInfo(tz))


def _in_legal(tz):
    t = _local(tz).time()
    return datetime.time(8, 0) <= t <= datetime.time(20, 30)


def _dow_enabled(db, tz):
    dow = int(_local(tz).strftime('%w'))
    with db.cursor() as cur:
        cur.execute('SELECT enabled, start_time, end_time FROM dialing_windows WHERE dow=%s',
                    (dow,))
        return cur.fetchone()


def _in_preference(db, tz):
    w = _dow_enabled(db, tz)
    if not w or not w['enabled']:
        return False
    t = _local(tz).time()
    return w['start_time'] <= t <= w['end_time']


ALL_OFFSET_ZONES = [
    'Pacific/Kiritimati', 'Pacific/Auckland', 'Australia/Sydney', 'Australia/Brisbane',
    'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Bangkok', 'Asia/Dhaka', 'Asia/Kolkata',
    'Asia/Karachi', 'Asia/Dubai', 'Europe/Moscow', 'Europe/Athens', 'Europe/Berlin',
    'Europe/London', 'Atlantic/Azores', 'America/Noronha', 'America/Sao_Paulo',
    'America/Halifax', 'America/New_York', 'America/Chicago', 'America/Denver',
    'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu', 'Pacific/Midway',
]


def _zone_with_local_hour(lo, hi):
    """A zone whose CURRENT local hour is in [lo, hi), or None."""
    for tz in ALL_OFFSET_ZONES:
        if lo <= _local(tz).hour < hi:
            return tz
    return None


def _zone_outside_legal_window():
    """
    A zone that is currently OUTSIDE 08:00-20:30 local.

    The legal window covers 12.5 of every 24 hours, so with zones spanning
    UTC-11 to UTC+14 such a zone always exists. Deterministic by construction
    - no skips, which matters because this is the only test that isolates the
    legal window from the (normally narrower) preference window.
    """
    for tz in ALL_OFFSET_ZONES:
        if not _in_legal(tz):
            return tz
    return None


def _lead(db, tz, company='W Firm', phone=None):
    phone = phone or '+1555' + str(abs(hash(tz)) % 10_000_000).zfill(7)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                          pool_status, status)
                       VALUES (%s,%s,%s,'active','new') RETURNING lead_id""",
                    (company, phone, tz))
        return cur.fetchone()['lead_id']


@pytest.fixture
def running_campaign(db, cfg_env):
    """A started, unpaused campaign for today."""
    date = campaigns.campaign_date(cfg_env)
    campaigns.ensure(cfg_env, date, daily_cap=200)
    campaigns.start(cfg_env, date)
    return date


def _enrol_and_select(db, cfg_env, date, lead_ids, source='fresh'):
    with db.cursor() as cur:
        for lid in lead_ids:
            cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                           VALUES (%s,%s,%s)""", (date, lid, source))
    db.commit()
    return dialer.select_and_claim(cfg_env, date=date, limit=100)


# --------------------------------------------------------------------------

def test_window_matches_real_local_time_in_every_zone(db, cfg_env, running_campaign):
    """
    The query must select exactly the zones that are inside BOTH the legal
    window and the operator preference window, right now.
    """
    ids = {tz: _lead(db, tz) for tz in ZONES}
    expected = {tz for tz in ZONES if _in_legal(tz) and _in_preference(db, tz)}
    claimed = _enrol_and_select(db, cfg_env, running_campaign, list(ids.values()))
    got = {tz for tz, lid in ids.items()
           if any(c['lead_id'] == lid for c in claimed)}
    assert got == expected, (
        f'\n  selected: {sorted(got)}\n  expected: {sorted(expected)}\n'
        f'  local times: { {tz: _local(tz).strftime("%a %H:%M") for tz in ZONES} }')


def test_a_lead_whose_local_time_is_after_2030_is_not_dialed(db, cfg_env, running_campaign):
    """The East-Coast-at-6pm-Pacific case, expressed generally."""
    tz = _zone_with_local_hour(21, 24)
    if tz is None:
        pytest.skip('no zone currently between 21:00 and 24:00')
    lid = _lead(db, tz)
    claimed = _enrol_and_select(db, cfg_env, running_campaign, [lid])
    assert claimed == [], f'{tz} is {_local(tz).strftime("%H:%M")} locally and was selected'


def test_a_lead_whose_local_time_is_before_0800_is_not_dialed(db, cfg_env, running_campaign):
    tz = _zone_with_local_hour(2, 7)
    if tz is None:
        pytest.skip('no zone currently between 02:00 and 07:00')
    lid = _lead(db, tz)
    claimed = _enrol_and_select(db, cfg_env, running_campaign, [lid])
    assert claimed == [], f'{tz} is {_local(tz).strftime("%H:%M")} locally and was selected'


def test_preference_window_can_narrow_but_never_widen(db, cfg_env, running_campaign):
    """
    THE TEST THAT ISOLATES THE LEGAL WINDOW.

    With the operator window opened to 24 hours, the ONLY thing standing
    between the dialer and a 3am phone call is the TCPA fragment. Note that
    with the default 09:00-17:00 preference the legal window is redundant -
    preference is strictly narrower - so this is the one place its removal is
    observable.
    """
    tz = _zone_outside_legal_window()
    assert tz is not None, 'no zone outside 08:00-20:30 - impossible across 26 offsets'
    with db.cursor() as cur:
        cur.execute("""UPDATE dialing_windows
                          SET enabled=true, start_time='00:00', end_time='23:59'""")
    db.commit()
    lid = _lead(db, tz)
    claimed = _enrol_and_select(db, cfg_env, running_campaign, [lid])
    assert claimed == [], (
        f'{tz} is {_local(tz).strftime("%H:%M")} locally - widening the operator '
        f'window must not defeat the legal window')


def test_disabling_the_weekday_stops_everything(db, cfg_env, running_campaign):
    """The preference window CAN narrow: disable today and nothing dials."""
    with db.cursor() as cur:
        cur.execute('UPDATE dialing_windows SET enabled=false')
    db.commit()
    ids = [_lead(db, tz) for tz in ZONES]
    claimed = _enrol_and_select(db, cfg_env, running_campaign, ids)
    assert claimed == []


def test_timezone_is_an_iana_name_not_an_offset(db):
    """An offset is wrong twice a year. The column must reject one."""
    import psycopg2
    with pytest.raises(psycopg2.Error):
        with db.cursor() as cur:
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                              pool_status, status)
                           VALUES ('X','+15550001111','-05:00','active','new')""")
            cur.execute("SELECT (now() AT TIME ZONE timezone) FROM leads WHERE phone_e164='+15550001111'")
    db.rollback()
