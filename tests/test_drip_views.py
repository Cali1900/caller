"""
THE DRIP AREA - /drips, separate from the call view.

⚠️ WHY SEPARATE AND NOT COLUMNS ON /leads. The two views want different facts
about the same firm: a call needs phone, attempts and scores; a sequence needs
step sent, clicks by step and next due. Both on one table gave seventeen columns
and a row that wrapped, and the shared list is the screen read first.

Lead DETAIL stays whole - calls and emails in ONE timeline - because that is the
one place the entire relationship belongs together.
"""
import pytest
from fastapi.testclient import TestClient

from api import campaigns, clicks, db as dbm, drip
from tests.test_drip import dripc, _lead, _join, open_all_hours  # noqa: F401


@pytest.fixture
def client(db, cfg_env):
    import api.web as web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _click(lead_id, seq):
    """A real click, through clicks.record(), against that send's own token."""
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT click_token FROM email_sends
                            WHERE lead_id = %s AND seq = %s""", (lead_id, seq))
            row = cur.fetchone()
    assert row and row['click_token'], \
        'the fixture produced a send with no token - a click test whose click ' \
        'does not happen passes for the wrong reason (see masked-guard row 12)'
    assert clicks.record(row['click_token']) is not None, \
        'clicks.record() did not resolve the token'


def test_the_drips_page_shows_email_columns_and_no_call_columns(db, dripc, client):
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Roster Firm')
    _join(db, lid, cid, sent_steps=2)

    r = client.get(f'/drips?drip={cid}')
    assert r.status_code == 200, r.text[:400]
    body = r.text
    for want in ('Firm', 'Contact', 'Email', 'Step sent', 'Last sent',
                 'Clicks by step', 'Next step due', 'Status'):
        assert want in body, f'the roster is missing the {want!r} column'
    assert 'Roster Firm' in body
    # ⚠️ AND NOT THE CALL COLUMNS. These mean nothing on a drip and their absence
    # is the point of the page, so it is asserted rather than assumed.
    for unwanted in ('Agent</a>', 'Outcome</a>', 'Last call</a>', '>Phone<'):
        assert unwanted not in body, f'{unwanted!r} leaked onto the drip view'


def test_the_per_step_table_attributes_clicks_to_the_RIGHT_step(db, dripc, client):
    """
    ⚠️ THE WHOLE REASON TO RUN A SEQUENCE: which email is doing the work.

    Two leads, both sent steps 1 and 2. ONE clicks step 2 only. So step 1 must
    read 2 sent / 0 clicked and step 2 must read 2 sent / 1 clicked - and a
    per-lead click count could not tell those apart, which is why the token is
    per SEND.
    """
    cid = dripc['campaign_id']
    a = _lead(db, days_ago=11, company='Clicker', phone_e164='+15553330077')
    b = _lead(db, days_ago=11, company='Quiet', phone_e164='+15553330078')
    _join(db, a, cid, sent_steps=2)
    _join(db, b, cid, sent_steps=2)
    _click(a, 2)                       # step 2 only, one lead

    stats = {s['position']: s for s in drip.step_stats(cid)}
    assert stats[1]['sent'] == 2 and stats[1]['clicked'] == 0, stats[1]
    assert stats[2]['sent'] == 2 and stats[2]['clicked'] == 1, stats[2]
    assert stats[2]['pct'] == 50.0, stats[2]
    assert stats[3]['sent'] == 0, 'an unsent step should count nothing'

    body = client.get(f'/drips?drip={cid}').text
    assert 'Per step' in body
    assert '50.0%' in body, 'the rate is not on the page'


def test_an_unsent_prepared_row_does_not_inflate_the_denominator(db, dripc):
    """
    sent_at IS NULL is a send that may still fail. Counting it would understate
    every rate on the page - and a rate that reads low is the number someone cuts
    an email over.
    """
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, phone_e164='+15553330079')
    _join(db, lid, cid, sent_steps=1)
    step2 = drip.steps(cid)[1]
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO email_sends
                               (lead_id, step_id, seq, to_email, click_token)
                           VALUES (%s,%s,2,'x@y.test','tok-prepared')""",
                        (lid, step2['step_id']))
    stats = {s['position']: s for s in drip.step_stats(cid)}
    assert stats[2]['sent'] == 0, \
        'a prepared-but-unsent row was counted as sent'


def test_the_roster_says_which_step_is_next_and_when(db, dripc, client):
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Next Firm', phone_e164='+15553330080')
    _join(db, lid, cid, sent_steps=2)
    rows = {r['company']: r for r in drip.roster(cid)}
    r = rows['Next Firm']
    assert r['steps_sent'] == 2 and r['last_step'] == 2
    assert r['next_step'] == 3, f'expected step 3 next, got {r["next_step"]}'
    assert r['next_due'] is not None, 'no due time for the next step'


def test_a_replied_lead_reads_as_stopped_not_as_due(db, dripc, client):
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Replied Firm', phone_e164='+15553330081')
    _join(db, lid, cid, sent_steps=1)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET replied_at = now() WHERE lead_id = %s",
                        (lid,))
    body = client.get(f'/drips?drip={cid}').text
    assert 'replied &mdash; stopped' in body or 'replied — stopped' in body, \
        'a replied lead must not read as having a step due'


def test_the_sequence_editor_is_on_the_drips_page(db, dripc, client):
    """One definition, included by both pages - not a second copy that can drift."""
    cid = dripc['campaign_id']
    body = client.get(f'/drips?drip={cid}').text
    assert 'name="subject_0"' in body, 'the sequence editor is missing'
    assert f'action="/campaign/{cid}/steps"' in body, \
        'the editor posts somewhere other than the save handler'


def test_the_leads_list_is_the_CALL_view_only(db, dripc, client):
    """
    The other half of the split, asserted on RENDERED HTML: the email columns are
    gone from /leads and the row is one line again.
    """
    lid = _lead(db, days_ago=2, company='Call View Firm',
                phone_e164='+15553330082')
    body = client.get('/?q=Call+View+Firm').text
    assert 'Call View Firm' in body
    assert '>Phone<' in body, 'the call view lost its phone column'
    for gone in ('Follow-up', 'Last email'):
        assert gone not in body, f'{gone!r} is still on the leads list'


def test_the_drips_page_works_with_no_drips_and_with_no_steps(db, client):
    """Two empty states that must not 500 - and must say what to do."""
    r = client.get('/drips')
    assert r.status_code == 200, r.text[:300]
    assert 'No drip campaigns yet' in r.text

    cid = campaigns.create('DRIP-EMPTY', campaign_type='drip')['campaign_id']
    r = client.get(f'/drips?drip={cid}')
    assert r.status_code == 200, r.text[:300]
    assert 'No steps yet' in r.text, 'a stepless drip must say it sends nothing'


def test_an_unknown_drip_id_falls_back_rather_than_404ing(db, dripc, client):
    """A stale bookmark should land somewhere useful."""
    r = client.get('/drips?drip=00000000-0000-0000-0000-000000000000')
    assert r.status_code == 200, r.text[:300]
    assert dripc['name'] in r.text


# ==========================================================================
# lead detail: calls and emails in ONE timeline
# ==========================================================================

def test_the_lead_timeline_shows_calls_and_emails_together(db, dripc, client):
    """
    ⚠️ THE ONE PLACE THE WHOLE RELATIONSHIP BELONGS TOGETHER - called, emailed,
    clicked, replied, in order. Taking the email columns off /leads is only
    defensible because this page keeps everything.
    """
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Whole Story', phone_e164='+15553330090')
    _join(db, lid, cid, sent_steps=2)
    _click(lid, 2)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            # A REAL call row. Activity rows of kind 'call' are skipped on
            # purpose - calls render from `calls` with their scores - so an
            # activity-only call would be correctly invisible and this test would
            # be asserting against a fixture, not the page.
            cur.execute("""INSERT INTO calls (call_id, lead_id, stage,
                                              call_status, disconnection_reason)
                           VALUES (gen_random_uuid()::text, %s, 'L1', 'ended',
                                   'user_hangup')""", (lid,))
            cur.execute("UPDATE leads SET replied_at = now() WHERE lead_id = %s",
                        (lid,))
    body = client.get(f'/leads/{lid}').text
    assert 'step 1 sent' in body, 'the timeline does not name the step'
    assert 'step 2 sent' in body
    assert 'CLICKED' in body and 'step 2' in body
    assert 'after that send' in body, 'the click delay is not shown'
    assert 'call ended' in body, 'the call left the timeline'


def test_one_send_is_ONE_timeline_entry(db, dripc, client):
    """
    ⚠️ A SEND WRITES TWICE: an activity row saying "drip step 2 sent" as prose, and
    an email_sends row that knows the step, subject and time. The timeline renders
    the second, so it must SKIP the first or every email appears twice.

    A drip STOP shares the activity kind and must NOT be skipped - it carries the
    reason, which is the whole point of recording it.
    """
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Once Only', phone_e164='+15553330091')
    _join(db, lid, cid, sent_steps=1)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'drip','drip step 1 sent','Following up')""",
                        (lid,))
            cur.execute("""INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'drip','drip STOPPED (bounced)','by worker')""",
                        (lid,))
    body = client.get(f'/leads/{lid}').text
    assert body.count('step 1 sent') == 1, \
        f"the send is on the timeline {body.count('step 1 sent')} times"
    assert 'drip STOPPED (bounced)' in body, \
        'a stop reason was skipped along with the send rows'


def test_a_bounce_is_visible_on_the_timeline(db, dripc, client):
    """
    A bounce that shows nowhere is a lead failing silently - the same argument
    that put 'bounced' on the status filter.
    """
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Bouncy', phone_e164='+15553330092')
    _join(db, lid, cid, sent_steps=1)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO email_audit (lead_id, to_email, outcome,
                                                    detail)
                           VALUES (%s,'gone@nowhere.test','send_failed',
                                   'mailbox does not exist')""", (lid,))
    body = client.get(f'/leads/{lid}').text
    assert 'send failed' in body, 'the failed send is invisible'
    assert 'mailbox does not exist' in body, 'the reason is not shown'


def test_a_prepared_but_unsent_row_says_so_rather_than_looking_sent(db, dripc, client):
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Pending', phone_e164='+15553330093')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE leads SET drip_campaign_id=%s WHERE lead_id=%s',
                        (cid, lid))
            cur.execute("""INSERT INTO email_sends
                               (lead_id, step_id, seq, to_email, click_token)
                           VALUES (%s,%s,1,'p@pending.test','tok-pending-1')""",
                        (lid, drip.steps(cid)[0]['step_id']))
    body = client.get(f'/leads/{lid}').text
    assert 'prepared, not sent' in body, \
        'an unsent row reads as a send, which is how a lost email hides'
