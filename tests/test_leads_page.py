"""
THE LEADS PAGE: upload, pagination, and the select-all that must not overreach.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm, web


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _leads(n, prefix='+1555900'):
    cid = c.create('LP-' + prefix)['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            for i in range(n):
                cur.execute("""INSERT INTO leads (company, phone_e164, timezone,
                                                  campaign_id, pool_status)
                               VALUES (%s,%s,'America/Los_Angeles',%s,'pool')""",
                            (f'Firm {i}', f'{prefix}{i:04d}', cid))
    return cid


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('per', [100, 200, 500])
def test_each_offered_page_size_works(db, client, per):
    assert client.get(f'/?per={per}').status_code == 200


def test_an_unoffered_page_size_falls_back_rather_than_erroring(db, client):
    """A hand-edited URL is not a reason to show an error page - but it is also
    not a way to ask for an unbounded page."""
    r = client.get('/?per=99999')
    assert r.status_code == 200
    assert 'value="100" selected' in r.text


def test_the_page_size_actually_limits_the_rows(db, client):
    _leads(120, '+1555901')
    body = client.get('/?per=100').text
    assert body.count('name="lead_id"') == 100
    body = client.get('/?per=200').text
    assert body.count('name="lead_id"') == 120


def test_the_page_size_survives_paging(db, client):
    _leads(120, '+1555902')
    r = client.get('/?per=100&page=2')
    assert r.status_code == 200
    assert r.text.count('name="lead_id"') == 20
    assert 'value="100" selected' in r.text


def test_the_page_count_uses_the_chosen_size(db, client):
    _leads(250, '+1555903')
    assert 'Page 1 of 3' in client.get('/?per=100').text
    assert 'Page 1 of 2' in client.get('/?per=200').text
    # 500 fits all 250 on one page, and the pager is hidden when there is only
    # one - so the absence of a pager IS the assertion here.
    assert 'Page 1 of' not in client.get('/?per=500').text


# ---------------------------------------------------------------------------
# SELECT ALL MUST NOT REACH PAST THE PAGE
# ---------------------------------------------------------------------------

def test_select_all_cannot_reach_leads_that_are_not_rendered(db, client):
    """
    THE PROPERTY THAT MATTERS. A "select all" that reaches past the visible
    rows is a checkbox that queues leads nobody has looked at.

    The checkbox is client-side, so what is actually testable - and what
    actually bounds it - is that the page renders ONLY `per` checkboxes. It
    cannot select what is not on the page.
    """
    _leads(300, '+1555904')
    body = client.get('/?per=100').text
    assert body.count('name="lead_id"') == 100, 'only this page may be selectable'
    assert 'id="selall"' in body


def test_the_select_all_script_is_scoped_to_the_table(db, client):
    """It queries inside the table, not the document - a document-wide query
    would pick up any future hidden inputs on the page."""
    body = client.get('/').text
    assert "querySelectorAll('table input[name=lead_id]')" in body


# ---------------------------------------------------------------------------
# upload lives HERE now
# ---------------------------------------------------------------------------

def test_the_upload_form_is_on_the_leads_page(db, client):
    """
    REGRESSION. The form was dropped from the campaign screen during the
    one-prompt-version change and existed nowhere for several commits - the
    /upload-form route was orphaned and nothing linked to it.
    """
    body = client.get('/').text
    assert 'action="/upload-form"' in body
    assert 'type="file"' in body


def test_uploading_lands_in_the_pool_and_returns_to_the_leads_page(db, client):
    csv = ('company,phone,timezone,website\n'
           'Pool Firm,+14245551212,America/Los_Angeles,https://poolfirm.com\n')
    r = client.post('/upload-form',
                    files={'file': ('leads.csv', csv, 'text/csv')},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers['location'].startswith('/?msg='), r.headers['location']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT pool_status, website FROM leads
                            WHERE phone_e164='+14245551212'""")
            row = cur.fetchone()
            assert row['pool_status'] == 'pool', 'upload must never queue'
            assert row['website'] == 'https://poolfirm.com'


def test_an_uploaded_lead_is_not_on_a_campaign(db, client):
    """Upload assigns nothing. Adding to a campaign is a separate, deliberate
    act - the same rule that keeps uploading from starting a dial."""
    csv = ('company,phone,timezone\n'
           'Unassigned,+14245551213,America/Los_Angeles\n')
    client.post('/upload-form', files={'file': ('l.csv', csv, 'text/csv')},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT campaign_id FROM leads WHERE phone_e164='+14245551213'")
            assert cur.fetchone()['campaign_id'] is None


def test_every_optional_column_actually_reaches_the_database(db, client):
    """
    REGRESSION, and the third instance of this shape today.

    `website` was added to upload.OPTIONAL - so it parsed, and was carried in
    the row dict - but the INSERT names its columns explicitly and did not list
    it. The value was read and silently dropped. Same family as
    campaigns.update() ignoring unknown fields and the dead settings keys: the
    write goes nowhere and says nothing.

    This asserts on ALL of them, so the next column added cannot repeat it.
    """
    from api import upload as up
    csv = ('company,phone,timezone,city,state,segment,external_ref,website\n'
           'Full Row,+14245551299,America/Los_Angeles,Encino,CA,pi,REF-9,'
           'https://fullrow.example\n')
    r = up.upload(csv)
    assert r['inserted'] == 1, r
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT {', '.join(up.OPTIONAL)} FROM leads
                             WHERE phone_e164='+14245551299'""")
            row = cur.fetchone()
    missing = [k for k in up.OPTIONAL if not row[k]]
    assert not missing, (
        f'these upload columns parse but never reach the database: {missing}. '
        f'Add them to the INSERT in upload.upload().')
