"""
DEMAND VOLUME and the PIPELINE FORECAST.

The number is a receptionist's estimate given from memory, in passing, to a
stranger. Everything here is built so that fact stays visible - and so that
"she did not answer" never becomes "she said zero".
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm, forecast, volume


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# parsing what she actually said
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('said,expect', [
    ('One hundred', 100), ('a hundred', 100), ('two hundred', 200),
    ('about 20', 20), ('probably 3', 3), ('twenty five', 25),
    ('couple', 2), ('zero', 0),
])
def test_a_clear_number_is_parsed(said, expect):
    assert volume.parse(said)[0] == expect


@pytest.mark.parametrize('said', ['no idea', 'not sure', 'depends on the month',
                                  "I don't know", 'varies', '', '   ', 'a few'])
def test_a_non_answer_is_NULL_not_zero(said):
    """
    THE RULE THAT MATTERS. She did not answer. Zero would say the firm sends
    no demands, and every forecast built on it would drift down each time
    somebody declined.
    """
    n, note = volume.parse(said)
    assert n is None, f'{said!r} parsed as {n}'
    assert note, 'and it must say why there is no number'


def test_a_declined_answer_reads_differently_from_an_unreadable_one():
    """"She did not know" and "we could not read it" are different facts."""
    assert 'did not know' in volume.parse('no idea')[1]
    assert 'not asked' in volume.parse('')[1]


@pytest.mark.parametrize('said,expect', [
    ('maybe 5 or 6', 5), ('10-15 a month', 10), ('between 20 and 30', 20),
])
def test_a_range_takes_the_low_end(said, expect):
    """A forecast that rounds every estimate up flatters itself, and she is
    guessing in the first place."""
    assert volume.parse(said)[0] == expect


def test_an_absurd_number_is_refused():
    assert volume.parse('99999')[0] is None


# ---------------------------------------------------------------------------
# the forecast
# ---------------------------------------------------------------------------

def _lead(cid, *, volume_n=None, emailed=False, clicked=False, demo=False,
          phone='+14245557700'):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                               campaign_id, demands_per_month, emailed_at, status)
                           VALUES ('F',%s,'America/Los_Angeles',%s,%s,
                                   CASE WHEN %s THEN now() END,
                                   CASE WHEN %s THEN 'demo_pending' ELSE 'new' END)
                           RETURNING lead_id""",
                        (phone, cid, volume_n, emailed or clicked or demo, demo))
            lid = cur.fetchone()['lead_id']
            if clicked:
                cur.execute('INSERT INTO email_clicks (lead_id) VALUES (%s)', (lid,))
    return lid


def test_monthly_value_is_volume_times_price(db):
    cid = c.create('FC1')['campaign_id']
    _lead(cid, volume_n=100, emailed=True, phone='+14245557701')
    out = forecast.build(cid)
    assert out['total_monthly'] == 100 * 150.0


def test_each_stage_carries_its_own_weight(db):
    cid = c.create('FC2')['campaign_id']
    _lead(cid, volume_n=100, emailed=True, phone='+14245557702')
    _lead(cid, volume_n=100, clicked=True, phone='+14245557703')
    _lead(cid, volume_n=100, demo=True, phone='+14245557704')
    out = forecast.build(cid)
    by = {r['stage']: r for r in out['rows']}
    assert by['emailed']['weighted'] == pytest.approx(15000 * 0.03)
    assert by['engaged']['weighted'] == pytest.approx(15000 * 0.15)
    assert by['demo_booked']['weighted'] == pytest.approx(15000 * 0.40)


def test_a_lead_with_no_volume_contributes_nothing_and_is_counted_separately(db):
    """
    NULL is not zero. It must add nothing AND be visible, so a total built from
    six of thirty leads is not mistaken for one built from thirty.
    """
    cid = c.create('FC3')['campaign_id']
    _lead(cid, volume_n=None, emailed=True, phone='+14245557705')
    out = forecast.build(cid)
    assert out['total_monthly'] == 0
    assert out['no_volume'] == 1
    assert out['with_volume'] == 0


def test_a_lead_not_yet_emailed_weighs_zero(db):
    """No forecastable value - not a small one."""
    cid = c.create('FC4')['campaign_id']
    _lead(cid, volume_n=100, phone='+14245557706')
    out = forecast.build(cid)
    assert out['total_weighted'] == 0
    assert out['total_monthly'] == 15000, 'unweighted still shows the ceiling'


def test_a_lead_counts_once_at_its_strongest_stage(db):
    """Clicked AND booked is one lead at demo_booked, not two rows."""
    cid = c.create('FC5')['campaign_id']
    _lead(cid, volume_n=10, clicked=True, demo=True, phone='+14245557707')
    out = forecast.build(cid)
    assert sum(r['leads'] for r in out['rows']) == 1
    assert [r['stage'] for r in out['rows'] if r['leads']] == ['demo_booked']


def test_the_price_is_a_campaign_setting(db):
    cid = c.create('FC6')['campaign_id']
    c.update(cid, price_per_demand=250)
    _lead(cid, volume_n=10, emailed=True, phone='+14245557708')
    assert forecast.build(cid)['total_monthly'] == 2500.0


def test_the_page_says_it_is_an_estimate(db, client):
    """A forecast presented as a bankable figure is worse than none, because
    it gets repeated."""
    cid = c.create('FC7')['campaign_id']
    _lead(cid, volume_n=100, emailed=True, phone='+14245557709')
    body = client.get(f'/funnel?campaign_id={cid}').text
    assert "receptionist's estimate" in body
    assert 'not a contract' in body


def test_the_list_sorts_by_volume_with_unknowns_last(db, client):
    """A firm that declined is not a firm that sends none - it must not sort
    among the zeros."""
    cid = c.create('FC8')['campaign_id']
    _lead(cid, volume_n=None, phone='+14245557710')
    _lead(cid, volume_n=90, phone='+14245557711')
    _lead(cid, volume_n=5, phone='+14245557712')
    body = client.get(f'/?sort=volume&campaign_id={cid}').text
    order = [m for m in ['+14245557711', '+14245557712', '+14245557710']
             if m in body]
    positions = [body.index(m) for m in order]
    assert positions == sorted(positions), 'high volume first, unknown last'
