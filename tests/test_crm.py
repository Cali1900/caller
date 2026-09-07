"""
The CRM.

Two properties carry real risk and are tested hardest:

  1. It is TUNNEL-ONLY. There is no login, which is safe only because
     caller-api binds 127.0.0.1 and nginx proxies exactly one path. A test
     asserts the app defines no auth of its own AND that the only route the
     public vhost exposes is the webhook.
  2. Company names and TRANSCRIPTS are text other people wrote. They must be
     escaped, never rendered raw.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns

LA = 'America/Los_Angeles'


@pytest.fixture
def client(db, cfg_env):
    import api.web as web
    from api.main import app
    return TestClient(app)


def _lead(db, company='Whitfield Law', phone='+15551110001', **kw):
    cols = {'company': company, 'phone_e164': phone, 'timezone': LA,
            'pool_status': 'active', 'status': 'new'}
    cols.update(kw)
    keys = ', '.join(cols)
    ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


def _scored_call(db, lead_id, call_id='c1', agent=8, outcome=10,
                 words='but Samantha does that', transcript='Agent: hi\nUser: ok'):
    with db.cursor() as cur:
        cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript,
                                          duration_ms, disconnection_reason, cost_cents)
                       VALUES (%s,%s,'L1',%s,45000,'user_hangup',20.5)""",
                    (call_id, lead_id, transcript))
        cur.execute("""INSERT INTO call_scores (call_id, lead_id, stage,
                          outcome_score, agent_score, agent_deductions,
                          what_happened, where_it_broke, their_words, we_got,
                          next_move, needs_human, model)
                       VALUES (%s,%s,'L1',%s,%s,ARRAY['talked_over'],
                               'gave_name_and_email','call succeeded',%s,
                               ARRAY['name','email'],'human_review',false,
                               'claude-opus-5')""",
                    (call_id, lead_id, outcome, agent, words))
    db.commit()


# --------------------------------------------------------------------------
# it lands on the leads list
# --------------------------------------------------------------------------

def test_root_is_the_leads_list_not_a_numbers_page(client, db):
    _lead(db, 'Whitfield Law')
    r = client.get('/')
    assert r.status_code == 200
    assert 'Whitfield Law' in r.text
    assert '<table' in r.text          # a list of firms, not a dashboard


def test_search_finds_a_firm_by_name_phone_and_email(client, db):
    _lead(db, 'Whitfield Law', '+15551110001', dm_email='sara@whitfieldlaw.com')
    _lead(db, 'Ortiz & Partners', '+15551110002')
    for q, expect, absent in [('Whitfield', 'Whitfield Law', 'Ortiz'),
                              ('5551110002', 'Ortiz', 'Whitfield'),
                              ('sara@', 'Whitfield Law', 'Ortiz')]:
        r = client.get('/', params={'q': q})
        assert expect in r.text, q
        assert absent not in r.text, q


def test_needs_you_filter_shows_only_leads_a_person_must_act_on(client, db):
    _lead(db, 'Fine Firm', '+15551110003')
    _lead(db, 'Unconfirmed Email Firm', '+15551110004',
          dm_email='x@y.com', dm_email_confirmed=False)
    r = client.get('/', params={'needs_you': '1'})
    assert 'Unconfirmed Email Firm' in r.text
    assert 'Fine Firm' not in r.text


# --------------------------------------------------------------------------
# the lead detail is the point of the phase
# --------------------------------------------------------------------------

def test_detail_shows_every_call_both_scores_and_what_they_said(client, db):
    lid = _lead(db)
    _scored_call(db, lid, 'c1', agent=8, outcome=10, words='but Samantha does that')
    _scored_call(db, lid, 'c2', agent=4, outcome=1, words="I'm not interested.")
    r = client.get(f'/leads/{lid}')
    assert r.status_code == 200
    # every call
    assert 'c1' in r.text and 'c2' in r.text
    # BOTH scores, separately
    assert '8/10' in r.text and '10/10' in r.text
    assert '4/10' in r.text and '1/10' in r.text
    # what they actually said
    assert 'but Samantha does that' in r.text
    assert 'not interested' in r.text
    # and the deduction detail
    assert 'talked_over' in r.text


def test_detail_shows_the_transcript(client, db):
    lid = _lead(db)
    _scored_call(db, lid, 'c1', transcript='Agent: who handles demand letters?')
    r = client.get(f'/leads/{lid}')
    assert 'who handles demand letters' in r.text


def test_an_unscored_call_says_so_rather_than_looking_empty(client, db):
    lid = _lead(db)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO calls (call_id, lead_id, stage, transcript)
                       VALUES ('c_ns',%s,'L1','t')""", (lid,))
    db.commit()
    r = client.get(f'/leads/{lid}')
    assert 'not scored yet' in r.text


# --------------------------------------------------------------------------
# fix a wrong email, mark DNC
# --------------------------------------------------------------------------

def test_you_can_fix_a_wrong_email(client, db):
    lid = _lead(db, dm_email='wrogn@typo.com', dm_email_confirmed=False)
    r = client.post(f'/leads/{lid}/edit', data={
        'dm_name': 'Sara Whitfield', 'dm_title': 'Intake',
        'dm_email': 'sara@whitfieldlaw.com', 'dm_email_confirmed': 'true',
        'notes': ''}, follow_redirects=False)
    assert r.status_code == 303
    with db.cursor() as cur:
        cur.execute('SELECT dm_email, dm_email_confirmed FROM leads WHERE lead_id=%s',
                    (lid,))
        row = cur.fetchone()
    assert row['dm_email'] == 'sara@whitfieldlaw.com'
    assert row['dm_email_confirmed'] is True


def test_an_email_correction_lands_on_the_timeline(client, db):
    """Otherwise nobody can tell a human fixed it from the agent capturing it."""
    lid = _lead(db, dm_email='wrong@x.com')
    client.post(f'/leads/{lid}/edit',
                data={'dm_email': 'right@x.com', 'dm_name': '', 'dm_title': '',
                      'dm_email_confirmed': '', 'notes': ''},
                follow_redirects=False)
    with db.cursor() as cur:
        cur.execute("SELECT summary, detail FROM activity WHERE lead_id=%s", (lid,))
        row = cur.fetchone()
    assert 'edited by hand' in row['summary']
    assert 'wrong@x.com' in row['detail'] and 'right@x.com' in row['detail']


def test_dnc_writes_suppression_AND_status_in_one_transaction(client, db):
    lid = _lead(db, phone='+15551119999')
    client.post(f'/leads/{lid}/dnc', follow_redirects=False)
    with db.cursor() as cur:
        cur.execute('SELECT status FROM leads WHERE lead_id=%s', (lid,))
        assert cur.fetchone()['status'] == 'dnc'
        cur.execute('SELECT reason, source FROM suppression WHERE phone_e164=%s',
                    ('+15551119999',))
        s = cur.fetchone()
    assert s is not None, 'status=dnc without a suppression row would still dial'
    assert s['reason'] == 'requested'


def test_a_dnc_lead_is_no_longer_a_dial_candidate(client, db, cfg_env, monkeypatch):
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    from api import dialer
    lid = _lead(db, phone='+15551118888')
    date = campaigns.campaign_date(cfg_env)
    campaigns.ensure(cfg_env, date); campaigns.start(cfg_env, date)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                       VALUES (%s,%s,'fresh')""", (date, lid))
    db.commit()
    assert len(dialer.select_and_claim(cfg_env, limit=10)) == 1

    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='new' WHERE lead_id=%s", (lid,))
    db.commit()
    client.post(f'/leads/{lid}/dnc', follow_redirects=False)
    assert dialer.select_and_claim(cfg_env, limit=10) == []


# --------------------------------------------------------------------------
# escaping - transcripts are text other people wrote
# --------------------------------------------------------------------------

def test_a_hostile_company_name_is_escaped_not_executed(client, db):
    _lead(db, company='<script>alert(1)</script> Law')
    r = client.get('/')
    assert '<script>alert(1)</script>' not in r.text
    assert '&lt;script&gt;' in r.text


def test_a_hostile_transcript_is_escaped(client, db):
    lid = _lead(db)
    _scored_call(db, lid, 'c1', words='<img src=x onerror=alert(1)>',
                 transcript='User: <script>alert(2)</script>')
    r = client.get(f'/leads/{lid}')
    assert '<script>alert(2)</script>' not in r.text
    assert 'onerror=alert(1)>' not in r.text
    assert '&lt;' in r.text


# --------------------------------------------------------------------------
# csv export
# --------------------------------------------------------------------------

def test_csv_export_works_and_respects_the_filter(client, db):
    _lead(db, 'Whitfield Law', '+15551110001', dm_email='sara@whitfieldlaw.com')
    _lead(db, 'Ortiz & Partners', '+15551110002')
    r = client.get('/export.csv')
    assert r.status_code == 200
    assert 'text/csv' in r.headers['content-type']
    assert 'attachment' in r.headers['content-disposition']
    assert 'Whitfield Law' in r.text and 'Ortiz & Partners' in r.text
    assert 'dm_email' in r.text.splitlines()[0]

    r2 = client.get('/export.csv', params={'q': 'Whitfield'})
    assert 'Whitfield Law' in r2.text and 'Ortiz' not in r2.text


# --------------------------------------------------------------------------
# the binding IS the auth
# --------------------------------------------------------------------------

def test_the_app_defines_no_login_and_that_is_deliberate(client):
    """
    If a login ever appears, this test should be deleted on purpose - not
    silently. Until then the safety property is the 127.0.0.1 binding plus an
    nginx vhost that proxies exactly one path.
    """
    from api.main import app
    paths = {r.path for r in app.routes}
    assert not {p for p in paths if 'login' in p or 'auth' in p}
    assert '/webhooks/retell' in paths


def test_the_public_vhost_exposes_only_the_webhook():
    """Reads the real nginx config rather than trusting the comment."""
    import os
    conf = '/etc/nginx/sites-enabled/caller-dev.counselorai.io'
    if not os.path.exists(conf):
        import pytest as _p
        _p.skip('nginx config not mounted into the test container')
    text = open(conf).read()
    assert 'location = /webhooks/retell' in text
    assert 'return 404' in text
