"""
BULK ADD — every lead matching the CURRENT FILTER.

Separate from the page-scoped select-all, which stays page-scoped so leads
nobody has looked at cannot be queued. This is the deliberate opposite, and it
has to be impossible to confuse with it.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _leads(n, state='NC', status='new', prefix='+1919555'):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            for i in range(n):
                cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                   state, pool_status, status)
                               VALUES (%s,%s,'America/New_York',%s,'pool',%s)""",
                            (f'Firm {i}', f'{prefix}{i:04d}', state, status))


def test_it_adds_every_matching_lead_not_just_the_page(db, client):
    """THE POINT: 1,100 leads is eleven rounds of select-and-add otherwise."""
    _leads(150, prefix='+1919551')
    cid = c.create('BULK')['campaign_id']
    r = client.post('/leads/queue-all', data={'campaign_id': cid},
                    follow_redirects=False)
    assert r.status_code == 303
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM leads
                            WHERE campaign_id=%s AND pool_status='active'""",
                        (cid,))
            assert cur.fetchone()['n'] == 150


def test_it_respects_the_active_filter(db, client):
    """
    Adding a set nobody meant is the failure this must not have. The filter is
    re-run SERVER-SIDE, not trusted from the page.
    """
    _leads(10, state='NC', prefix='+1919552')
    _leads(4, state='CA', prefix='+1310552')
    cid = c.create('BULK-F')['campaign_id']
    client.post('/leads/queue-all', data={'campaign_id': cid, 'q': 'Firm'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM leads WHERE campaign_id=%s", (cid,))
            all_matching = cur.fetchone()['n']
    assert all_matching == 14, 'q=Firm matches both states'

    cid2 = c.create('BULK-F2')['campaign_id']
    client.post('/leads/queue-all',
                data={'campaign_id': cid2, 'status': 'new', 'q': '+1310552'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM leads WHERE campaign_id=%s", (cid2,))
            assert cur.fetchone()['n'] == 4, 'only the CA phones'


def test_the_count_comes_from_the_database_not_the_page(db, client):
    """
    A hidden field saying "1100" is a number the browser was told, not one the
    database agrees with - and the list can change between render and click.
    """
    _leads(7, prefix='+1919553')
    cid = c.create('BULK-C')['campaign_id']
    r = client.post('/leads/queue-all',
                    data={'campaign_id': cid, 'total': '99999'},
                    follow_redirects=False)
    assert '7 lead' in r.headers['location'].replace('%20', ' ')


def test_the_button_states_the_real_count_and_the_filter(db, client):
    """'the button text must state the actual count, not "all"'."""
    _leads(12, prefix='+1919554')
    body = client.get('/?status=new').text
    assert 'Add all 12 matching' in body
    # The confirm now reuses the filter CHIPS, which label as "status: new".
    # Same fact, one implementation - see _filter_description.
    assert 'status: new' in body


def test_no_filter_says_so_out_loud(db, client):
    _leads(3, prefix='+1919555')
    body = client.get('/').text
    assert 'NO FILTER' in body, 'an unfiltered bulk add must announce itself'


def test_it_refuses_without_a_campaign(db, client):
    _leads(3, prefix='+1919556')
    r = client.post('/leads/queue-all', data={}, follow_redirects=False)
    assert 'pick+a+campaign' in r.headers['location']


def test_it_never_dials(db, client):
    """Adding assigns and queues. Nothing dials until the campaign runs."""
    _leads(5, prefix='+1919557')
    cid = c.create('BULK-ND')['campaign_id']
    r = client.post('/leads/queue-all', data={'campaign_id': cid},
                    follow_redirects=False)
    assert 'Nothing dials' in r.headers['location'].replace('%20', ' ')
    assert c.get(cid)['is_running'] is False


def test_the_page_scoped_control_is_still_page_scoped(db, client):
    """The whole reason the bulk button is SEPARATE."""
    _leads(150, prefix='+1919558')
    body = client.get('/?per=100').text
    assert body.count('name="lead_id"') == 100
    assert 'Add selected to campaign' in body
    assert 'Add all' in body
    assert body.index('Add selected') < body.index('Add all'), \
        'the safe one comes first'


def test_the_confirm_names_every_filter_not_just_the_first_six(db, client):
    """
    THE FAULT THIS CLOSES. _filter_description listed six of the seventeen
    filters _lead_query accepts, so the confirm could read "status: new"
    while the button was about to add every lead in North Carolina too. Sean
    hit exactly this shape once already: "2 matching: state = NC" while it
    added everything.

    state, city and tag are all in the group the old version could not see.
    """
    _leads(3, prefix='+1919556')
    body = client.get('/?status=new&state=NC&city=Raleigh').text

    # SCOPED TO THE CONFIRM TEXT, not the page. The removable filter pills
    # render the same labels elsewhere on this page, so asserting over the
    # whole body passes whether or not the confirm names anything - a masked
    # guard, caught by break 89 going green.
    import re as _re
    m = _re.search(r'Matching: ([^\\]*)\\n', body)
    assert m, 'the bulk-add confirm has no "Matching:" line at all'
    confirm = m.group(1)
    for expected in ('status: new', 'state: NC', 'city: Raleigh'):
        assert expected in confirm, \
            f'the confirm did not name {expected!r}; it said {confirm!r}'
