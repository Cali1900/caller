"""
SORTING AND FILTERING at 1,085 leads.

The recurring bug in this area is not the filter - it is the COUNT QUERY
drifting from the LIST QUERY. It has happened three times: the clicks join,
the campaign join, and the score/email laterals. Each time the symptom was a
500 or a count that silently disagreed with the rows on screen.

So the first test here is about the two queries, not about any one filter.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm, web


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _lead(**kw):
    cols = {'company': 'Firm', 'phone_e164': '+19195550000',
            'timezone': 'America/New_York', 'state': 'NC', 'city': 'Raleigh',
            'pool_status': 'pool', 'status': 'new'}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                        list(cols.values()))
            return cur.fetchone()['lead_id']


def _scored(phone, agent, outcome, state='NC'):
    lid = _lead(phone_e164=phone, state=state)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript,
                                              agent_version)
                           VALUES (%s,%s,'L1','t',15)""", (f'c_{phone}', lid))
            cur.execute("""INSERT INTO call_scores (call_id, lead_id, stage,
                               outcome_score, agent_score, agent_deductions,
                               what_happened, where_it_broke, their_words,
                               we_got, next_move, needs_human, model)
                           VALUES (%s,%s,'L1',%s,%s,'{}','not_interested','x','y',
                                   '{}','drop',false,'m')""",
                        (f'c_{phone}', lid, outcome, agent))
    return lid


# ---------------------------------------------------------------------------
# the count query and the list query must never drift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('qs', [
    '', 'state=NC', 'city=Raleigh', 'has_email=yes', 'has_email=no',
    'called=yes', 'called=no', 'agent_min=5', 'agent_max=5',
    'outcome_min=8', 'vol_min=10', 'vol_max=100', 'status=new',
    'sort=agent&dir=desc', 'sort=calls&dir=asc', 'sort=volume&dir=desc',
    'state=NC&has_email=no&called=no',
])
def test_every_filter_and_sort_renders(db, client, qs):
    """
    A 500 here reads as "no matches" to anyone counting rows - which is how a
    broken filter looked like a working one.
    """
    _lead(phone_e164='+19195550001')
    r = client.get(f'/?{qs}')
    assert r.status_code == 200, f'{qs} -> {r.status_code}'


def test_the_header_count_matches_the_rows(db, client):
    """
    The count comes from a SEPARATE query. If it drifts from the list query the
    header says one thing and the table shows another, and neither is obviously
    wrong.
    """
    for i in range(7):
        _lead(phone_e164=f'+1919555{i:04d}', state='NC')
    _lead(phone_e164='+13105559999', state='CA')
    body = client.get('/?state=NC&per=100').text
    assert body.count('name="lead_id"') == 7
    assert '7 matching' in body or '7 leads' in body


# ---------------------------------------------------------------------------
# the filters
# ---------------------------------------------------------------------------

def test_score_ranges(db, client):
    _scored('+19195551001', agent=9, outcome=2)
    _scored('+19195551002', agent=4, outcome=9)
    assert client.get('/?agent_min=8').text.count('name="lead_id"') == 1
    assert client.get('/?agent_max=5').text.count('name="lead_id"') == 1
    assert client.get('/?outcome_min=8').text.count('name="lead_id"') == 1


def test_a_volume_range_excludes_unknowns_rather_than_treating_them_as_zero(db, client):
    """
    "She did not answer" is not "sends none". A range starting at 0 that swept
    up every unanswered lead would be silently wrong.
    """
    _lead(phone_e164='+19195552001', demands_per_month=None)
    _lead(phone_e164='+19195552002', demands_per_month=5)
    body = client.get('/?vol_min=0&vol_max=1000').text
    assert body.count('name="lead_id"') == 1, 'the unknown must not be swept in'


def test_has_email_and_called(db, client):
    _lead(phone_e164='+19195553001', dm_email='a@b.com')
    _lead(phone_e164='+19195553002')
    assert client.get('/?has_email=yes').text.count('name="lead_id"') == 1
    assert client.get('/?has_email=no').text.count('name="lead_id"') == 1
    assert client.get('/?called=no').text.count('name="lead_id"') == 2
    assert client.get('/?called=yes').text.count('name="lead_id"') == 0


def test_filters_combine(db, client):
    _lead(phone_e164='+19195554001', state='NC', dm_email='a@b.com')
    _lead(phone_e164='+19195554002', state='NC')
    _lead(phone_e164='+13105554003', state='CA', dm_email='c@d.com')
    assert client.get('/?state=NC&has_email=yes').text.count('name="lead_id"') == 1


# ---------------------------------------------------------------------------
# sorting
# ---------------------------------------------------------------------------

def test_sorting_reverses(db, client):
    for name, phone in (('Alpha', '+19195555001'), ('Zulu', '+19195555002')):
        _lead(company=name, phone_e164=phone)
    asc = client.get('/?sort=firm&dir=asc').text
    desc = client.get('/?sort=firm&dir=desc').text
    assert asc.index('Alpha') < asc.index('Zulu')
    assert desc.index('Zulu') < desc.index('Alpha')


def test_the_arrow_marks_the_sorted_column(db, client):
    _lead(phone_e164='+19195556001')
    body = client.get('/?sort=firm&dir=asc').text
    import re
    marked = re.findall(r'sort=(\w+)[^"]*"\s*>[^<]*<span class="arr">', body)
    assert marked == ['firm'], f'exactly one column marked, got {marked}'


def test_unknowns_sort_last_in_BOTH_directions(db, client):
    """
    "Never called" is an absence, not a zero. It belongs at the bottom
    whichever way the arrow points - otherwise reversing the sort fills the
    first page with leads that have no data at all.
    """
    _scored('+19195557001', agent=9, outcome=9)
    _lead(phone_e164='+19195557002')          # no score at all
    for d in ('asc', 'desc'):
        body = client.get(f'/?sort=agent&dir={d}').text
        assert body.index('+19195557001') < body.index('+19195557002'), \
            f'the unscored lead must sort last with dir={d}'


def test_the_sort_survives_paging_and_filtering(db, client):
    for i in range(12):
        _lead(company=f'F{i:02d}', phone_e164=f'+1919556{i:04d}')
    body = client.get('/?sort=firm&dir=asc&per=100&state=NC&page=1').text
    assert 'sort=firm' in body and 'dir=asc' in body, 'the links carry the sort'


def test_an_unknown_sort_key_falls_back_rather_than_erroring(db, client):
    _lead(phone_e164='+19195558001')
    assert client.get('/?sort=DROP+TABLE&dir=x').status_code == 200


# ---------------------------------------------------------------------------
# chips
# ---------------------------------------------------------------------------

def test_active_filters_show_as_removable_chips(db, client):
    _lead(phone_e164='+19195559001')
    body = client.get('/?state=NC&has_email=no&agent_min=3').text
    assert 'state: NC' in body
    assert 'no email' in body
    assert 'agent 3-10' in body
    assert body.count('class="chip"') == 3


def test_no_filter_says_so(db, client):
    _lead(phone_e164='+19195559002')
    assert 'no filter' in client.get('/').text


def test_the_bulk_add_still_respects_the_filter(db, client):
    """Confirming what Sean asked me to confirm, against the NEW filters."""
    _lead(phone_e164='+19195559101', state='NC')
    _lead(phone_e164='+19195559102', state='NC')
    _lead(phone_e164='+13105559103', state='CA')
    cid = c.create('SF-bulk')['campaign_id']
    client.post('/leads/queue-all', data={'campaign_id': cid, 'state': 'NC'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM leads WHERE campaign_id=%s", (cid,))
            assert cur.fetchone()['n'] == 2, 'only the NC leads'


# --------------------------------------------------------------------------
# archived is out of every working view
# --------------------------------------------------------------------------

def test_archived_leads_are_hidden_from_the_list_and_the_count(client, db):
    """
    ISOLATED: both leads are identical but for status. No other filter is
    applied, so status is the only thing that can exclude either of them.

    The COUNT is asserted alongside the rows on purpose - a count that drifts
    from the list is the recurring bug in this area, and it has shipped three
    times. A header reading 2 above one row is worse than showing both.
    """
    _lead(company='Working Firm', phone_e164='+19195559001')
    _lead(company='Resting Firm', phone_e164='+19195559002', status='archived')
    body = client.get('/').text
    assert 'Working Firm' in body
    assert 'Resting Firm' not in body, \
        'an archived lead appeared in the default working view'
    assert '>1<' in body or ' 1 ' in body


def test_asking_for_archived_shows_them_and_nothing_else(client, db):
    """Reachable, but only by asking. Otherwise the reason a lead is resting
    is invisible and the return queue cannot be worked."""
    _lead(company='Working Firm', phone_e164='+19195559003')
    _lead(company='Resting Firm', phone_e164='+19195559004',
          status='archived', archive_reason='refused')
    body = client.get('/?status=archived').text
    assert 'Resting Firm' in body
    assert 'Working Firm' not in body


def test_the_bulk_add_never_sweeps_an_archived_lead_onto_a_campaign(client, db):
    """
    The bulk add reuses _lead_query's WHERE clause. If the archived filter
    lived on the page rather than in the query, "add all N matching" would
    quietly pull resting leads back onto a live campaign - undoing the whole
    point of the archive, at scale, in one click.
    """
    from api import web as _web
    _lead(company='Working Firm', phone_e164='+19195559005')
    _lead(company='Resting Firm', phone_e164='+19195559006', status='archived')
    _sql, params, where, cparams = _web._lead_query(
        '', '', '', '', 100, 0)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n {_web._LEAD_JOINS} "
                        f"WHERE {' AND '.join(where)}", cparams)
            assert cur.fetchone()['n'] == 1, \
                'the bulk-add count included an archived lead'
