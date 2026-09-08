"""
THE FUNNEL.

A COHORT funnel: a date range selects the leads FIRST DIALED in that window
and follows that same set down. An event funnel would count a Monday call and
a Thursday email in the same column and produce percentages nobody can read.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm, funnel


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _cohort(n, *, dialed=0, reached=0, named=0, emailed_cap=0, emailed=0,
            clicked=0, replied=0, demo=0, version=15, days_ago=1, prefix='+1555800'):
    """Build a lead set with exact counts at each step."""
    cid = c.create('FN-' + prefix[-4:])['campaign_id']
    when = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    ids = []
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            for i in range(n):
                cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                   campaign_id, pool_status,
                                   first_dialed_at, dm_name, dm_email,
                                   emailed_at, replied_at, status)
                               VALUES (%s,%s,'America/Los_Angeles',%s,'active',
                                       %s,%s,%s,%s,%s,%s)
                               RETURNING lead_id""",
                            (f'F{i}', f'{prefix}{i:04d}', cid,
                             when if i < dialed else None,
                             'Name' if i < named else None,
                             f'e{i}@f.example' if i < emailed_cap else None,
                             when if i < emailed else None,
                             when if i < replied else None,
                             'demo_pending' if i < demo else 'new'))
                lid = cur.fetchone()['lead_id']
                ids.append(lid)
                if i < reached:
                    cur.execute("""INSERT INTO calls (call_id, lead_id, stage,
                                       transcript, agent_version, created_at)
                                   VALUES (%s,%s,'L1','spoke',%s,%s)""",
                                (f'c_{prefix}_{i}', lid, version, when))
                elif i < dialed:
                    cur.execute("""INSERT INTO calls (call_id, lead_id, stage,
                                       transcript, agent_version, created_at)
                                   VALUES (%s,%s,'L1','',%s,%s)""",
                                (f'c_{prefix}_{i}', lid, version, when))
                if i < clicked:
                    cur.execute("""INSERT INTO email_clicks (lead_id, clicked_at)
                                   VALUES (%s,%s)""", (lid, when))
    return cid, ids


def _by_key(rows):
    return {r['key']: r for r in rows}


def test_the_percentages_are_of_the_previous_step(db):
    """
    A percentage of the TOP makes every late step look terrible and hides
    which one is actually leaking.
    """
    cid, _ = _cohort(100, dialed=34, reached=8, named=4, emailed_cap=3,
                     emailed=3, clicked=1, prefix='+1555801')
    r = _by_key(funnel.build(campaign_id=cid)['rows'])
    assert r['in_queue']['n'] == 100 and r['in_queue']['pct'] is None
    assert r['dialed']['pct'] == 34.0            # of 100
    assert r['reached']['pct'] == pytest.approx(23.5, abs=0.1)   # 8 of 34
    assert r['named']['pct'] == 50.0             # 4 of 8
    assert r['emailed_cap']['pct'] == 75.0       # 3 of 4
    assert r['clicked']['pct'] == pytest.approx(33.3, abs=0.1)   # 1 of 3


def test_the_drop_is_reported_at_each_step(db):
    cid, _ = _cohort(10, dialed=10, reached=4, named=4, emailed_cap=4,
                     emailed=4, prefix='+1555802')
    r = _by_key(funnel.build(campaign_id=cid)['rows'])
    assert r['reached']['drop_n'] == 6
    assert r['reached']['drop_pct'] == 60.0


def test_the_worst_step_is_the_biggest_proportional_drop(db):
    """Not the biggest absolute one - a step that loses 6 of 10 is worse than
    one that loses 60 of 1000."""
    cid, _ = _cohort(100, dialed=90, reached=20, named=18, emailed_cap=17,
                     emailed=17, clicked=5, prefix='+1555803')
    rows = funnel.build(campaign_id=cid)['rows']
    worst = [r for r in rows if r['worst']]
    assert len(worst) == 1, 'exactly one worst step'
    assert worst[0]['key'] == 'reached', f"got {worst[0]['key']}"


def test_a_thin_funnel_names_no_worst_step(db):
    """
    A funnel with almost nothing in it has not TESTED its steps. Naming a
    worst one would point at the wrong thing every time a campaign is new.
    """
    cid, _ = _cohort(2, dialed=2, reached=1, prefix='+1555804')
    out = funnel.build(campaign_id=cid)
    assert out['thin'] is True
    assert not any(r['worst'] for r in out['rows'])


def test_outcomes_never_compete_for_worst_step(db):
    """
    replied and demo hang off `emailed` alongside clicked - they are outcomes
    of one step, not a sequence. A 100% "drop" to zero demos is not a leak.
    """
    cid, _ = _cohort(50, dialed=50, reached=50, named=50, emailed_cap=50,
                     emailed=50, clicked=40, replied=0, demo=0,
                     prefix='+1555805')
    rows = funnel.build(campaign_id=cid)['rows']
    worst = [r['key'] for r in rows if r['worst']]
    assert 'replied' not in worst and 'demo' not in worst


def test_filtering_by_campaign_isolates_the_cohort(db):
    a, _ = _cohort(10, dialed=10, reached=10, prefix='+1555806')
    b, _ = _cohort(4, dialed=1, reached=0, prefix='+1555807')
    assert funnel.build(campaign_id=a)['counts']['in_queue'] == 10
    assert funnel.build(campaign_id=b)['counts']['dialed'] == 1


def test_filtering_by_prompt_version_answers_did_the_script_move_it(db):
    """The point of the screen: did that change move a specific step."""
    cid, _ = _cohort(10, dialed=10, reached=9, version=15, prefix='+1555808')
    _cohort(10, dialed=10, reached=2, version=17, prefix='+1555809')
    v15 = funnel.build(agent_version=15)
    v17 = funnel.build(agent_version=17)
    assert v15['counts']['reached'] == 9
    assert v17['counts']['reached'] == 2


def test_a_date_range_selects_the_cohort_first_dialed_in_it(db):
    """Not events in the window - the LEADS that entered the funnel in it."""
    _cohort(5, dialed=5, reached=5, days_ago=30, prefix='+1555810')
    _cohort(3, dialed=3, reached=1, days_ago=1, prefix='+1555811')
    today = datetime.date.today()
    recent = funnel.build(date_from=(today - datetime.timedelta(days=3)).isoformat())
    assert recent['counts']['dialed'] == 3
    assert recent['counts']['reached'] == 1


def test_the_page_renders_and_marks_the_worst_step(db, client):
    cid, _ = _cohort(100, dialed=90, reached=20, named=18, emailed_cap=17,
                     emailed=17, clicked=5, prefix='+1555812')
    body = client.get(f'/funnel?campaign_id={cid}').text
    assert 'WORST STEP' in body
    assert body.count('WORST STEP') == 1


def test_division_by_zero_never_happens(db):
    """An empty funnel must render, not explode."""
    cid = c.create('FN-empty')['campaign_id']
    out = funnel.build(campaign_id=cid)
    assert out['counts']['in_queue'] == 0
    assert all(r['pct'] is None or r['pct'] == 0 for r in out['rows'])
