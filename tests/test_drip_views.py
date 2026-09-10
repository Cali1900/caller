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
    # 'Sends' and 'In the sequence' replaced 'Next step due' and 'Status' on
    # 2026-09-10: the first showed delay arithmetic rather than when the mail
    # would go, and the second showed the CALL status, which means nothing here.
    for want in ('Firm', 'Contact', 'Email', 'Step sent', 'Last sent',
                 'Clicks by step', 'Sends', 'In the sequence'):
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
    assert 'stopped' in body and 'replied' in body, \
        'a replied lead must not read as having a step due'
    row = [r for r in drip.roster(cid) if r['lead_id'] == lid][0]
    assert row['drip_state'] == 'stopped' and row['state_detail'] == 'replied', row


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


# ==========================================================================
# a partial POST must never blank a field it did not mention
# ==========================================================================

def test_a_config_post_without_notes_does_not_blank_them(db, client):
    """
    ⚠️ THIS COST A REAL FIELD. `notes: str = Form('')` meant an absent field
    arrived as an empty string and was written, so a config POST that did not
    mention notes ERASED them - silently, with a success banner. C1's notes went
    that way while verifying an unrelated selector.

    ABSENT IS NOT EMPTY. None means "not on the form", '' means "the operator
    cleared the box". The real form posts every field, so clearing still works.
    """
    from tests.conftest import running_campaign_id
    cid = running_campaign_id()
    campaigns.update(cid, notes='keep me')
    base = {'name': 'C-T', 'agent_l1_version': '1',
            'sender_email': campaigns.get(cid)['sender_email'],
            'sender_name': 'S', 'sender_company_line': 'C', 'daily_cap': '100',
            'max_concurrent': '1', 'dial_interval_min': '210',
            'dial_interval_max': '300'}

    client.post(f'/campaign/{cid}/save', data=base, follow_redirects=False)
    assert campaigns.get(cid)['notes'] == 'keep me', \
        'a POST that never mentioned notes erased them'

    # AND CLEARING STILL WORKS, because the form does post the field.
    client.post(f'/campaign/{cid}/save', data={**base, 'notes': ''},
                follow_redirects=False)
    assert (campaigns.get(cid)['notes'] or '') == '', \
        'the operator can no longer clear the notes box'


def test_a_partial_lead_edit_does_not_blank_the_contact_facts(db, dripc, client):
    """
    ⚠️ THE SAME BUG, SOMEWHERE WORSE. lead_edit wrote dm_name, dm_title,
    dm_email, dm_email_confirmed, demands_per_month and notes unconditionally
    through NULLIF(%s,''), so any post that omitted one NULLed it - dm_email
    included, which ends a drip silently: the address is the only thing a step
    can be sent to.

    The PHONE was already protected against exactly this, with a comment saying
    why, and the protection was never generalised to the fields beside it.
    """
    lid = _lead(db, days_ago=1, company='Facts Firm', phone_e164='+15553330094',
                dm_name='Pat Kelly', dm_email='pat@facts.test')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads SET dm_title = 'Partner', notes = 'keep',
                                  demands_per_month = 12
                            WHERE lead_id = %s""", (lid,))

    # a post that mentions ONLY the title - everything else absent
    client.post(f'/leads/{lid}/edit', data={'dm_title': 'Managing Partner'},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lid,))
            got = cur.fetchone()
    assert got['dm_title'] == 'Managing Partner', 'the edit did not apply'
    assert got['dm_email'] == 'pat@facts.test', \
        'a partial edit erased the email address - the drip would stop silently'
    assert got['dm_name'] == 'Pat Kelly', 'a partial edit erased the contact name'
    assert got['notes'] == 'keep', 'a partial edit erased the notes'
    assert got['demands_per_month'] == 12, 'a partial edit erased the volume'


def test_clearing_a_lead_field_on_purpose_still_works(db, client):
    """The other half: an EMPTY box posted by the real form still clears."""
    lid = _lead(db, days_ago=1, company='Clearable', phone_e164='+15553330095',
                dm_name='Someone')
    client.post(f'/leads/{lid}/edit',
                data={'dm_name': '', 'dm_title': '', 'dm_email': '',
                      'notes': '', 'demands_per_month': ''},
                follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT dm_name FROM leads WHERE lead_id = %s', (lid,))
            assert cur.fetchone()['dm_name'] is None, \
                'an empty box no longer clears the field'


# ==========================================================================
# the wiring, visible from BOTH ends and warned about before it bites
# ==========================================================================

def test_a_drip_says_which_call_campaigns_feed_it(db, dripc, client):
    """
    ⚠️ default_drip_id LIVES ON THE CALL CAMPAIGN, so the drip had no way to say
    who feeds it. A relationship visible from one side only is one nobody can
    audit - and auditing it meant running a query, which is not a UI.
    """
    from tests.conftest import running_campaign_id
    cid = dripc['campaign_id']
    call_id = running_campaign_id()
    campaigns.update(call_id, default_drip_id=cid)

    fed = campaigns.feeders(cid)
    assert [f['campaign_id'] for f in fed] == [call_id], fed

    # on the drip's own config page AND in the drip area
    for url in (f'/campaign/{cid}', f'/drips?drip={cid}'):
        body = client.get(url).text
        assert 'Fed by' in body, f'{url} does not say who feeds this drip'
        assert campaigns.get(call_id)['name'] in body, \
            f'{url} does not name the feeding campaign'


def test_many_call_campaigns_can_feed_one_drip(db, dripc, client):
    """Many-to-one is allowed and normal - nothing enforces exclusivity."""
    from tests.conftest import running_campaign_id
    cid = dripc['campaign_id']
    a = running_campaign_id()
    b = campaigns.create('C-SECOND', campaign_type='call')['campaign_id']
    campaigns.update(a, default_drip_id=cid)
    campaigns.update(b, default_drip_id=cid)
    assert len(campaigns.feeders(cid)) == 2, campaigns.feeders(cid)
    body = client.get(f'/campaign/{cid}').text
    assert 'C-SECOND' in body


def test_stopping_a_fed_drip_asks_first(db, dripc, client):
    """
    ⚠️ STOPPING A DRIP QUIETLY CHANGES WHAT HAPPENS TO EVERY FUTURE LEAD of every
    campaign pointing at it: drip_for() refuses to route into a stopped drip, so
    email 1 goes out and nothing follows. Nothing is orphaned in the database -
    the wiring is kept - but the CONSEQUENCE is invisible without a confirmation.
    """
    from tests.conftest import running_campaign_id
    cid = dripc['campaign_id']
    campaigns.update(running_campaign_id(), default_drip_id=cid)
    assert campaigns.get(cid)['is_running'], 'the fixture drip should be running'

    r = client.post(f'/campaign/{cid}/stop', follow_redirects=False)
    assert 'stop_confirm' in r.headers['location'], r.headers['location']
    assert campaigns.get(cid)['is_running'], \
        'the drip stopped without asking, and its feeders were not named'

    body = client.get(f'/campaign/{cid}?stop_confirm=1').text
    assert 'no drip at all' in body, 'the confirmation does not say what breaks'

    r = client.post(f'/campaign/{cid}/stop', data={'confirm': 'yes'},
                    follow_redirects=False)
    assert not campaigns.get(cid)['is_running'], 'confirming did not stop it'


def test_stopping_an_unfed_drip_does_not_ask(db, dripc, client):
    """No feeders, nothing to warn about - a confirmation nobody needs is noise."""
    cid = dripc['campaign_id']
    assert campaigns.feeders(cid) == []
    r = client.post(f'/campaign/{cid}/stop', follow_redirects=False)
    assert 'stop_confirm' not in r.headers['location'], r.headers['location']
    assert not campaigns.get(cid)['is_running']


def test_today_warns_about_the_CONFIG_before_any_lead_is_affected(db, dripc, client):
    """
    ⚠️ WHY THE "emailed and on no drip" NET WAS NOT ENOUGH. That net matches
    emailed_at IS NOT NULL AND drip_campaign_id IS NULL - it can only fire once a
    lead is ALREADY past email 1, by which point the mail has gone and nothing
    follows it. The failure was a campaign CONFIGURED to send people nowhere,
    which is observable before anyone is harmed and was completely silent.
    """
    from tests.conftest import running_campaign_id
    call_id = running_campaign_id()
    campaigns.update(call_id, default_drip_id=None)

    problems = campaigns.wiring_problems()
    assert any(w['campaign_id'] == call_id and 'no follow-up drip' in w['why']
               for w in problems), problems
    body = client.get('/today').text
    assert 'put the lead on no drip' in body, '/today does not warn on the config'
    assert campaigns.get(call_id)['name'] in body

    # AND THE STOPPED-DRIP VARIANT, which is the shape that actually bit: the
    # wiring is set and the destination is off.
    campaigns.update(call_id, default_drip_id=dripc['campaign_id'])
    campaigns.stop(dripc['campaign_id'])
    problems = campaigns.wiring_problems()
    assert any('is stopped' in w['why'] for w in problems), problems
    assert 'is stopped' in client.get('/today').text


def test_a_correctly_wired_campaign_produces_no_warning(db, dripc, client):
    """The other half: a warning that never clears is a warning nobody reads."""
    from tests.conftest import running_campaign_id
    call_id = running_campaign_id()
    campaigns.update(call_id, default_drip_id=dripc['campaign_id'])
    assert campaigns.get(dripc['campaign_id'])['is_running']
    assert campaigns.wiring_problems() == [], campaigns.wiring_problems()
    assert 'put the lead on no drip' not in client.get('/today').text


# ==========================================================================
# the roster answers drip questions, in drip terms
# ==========================================================================

def test_the_status_column_is_the_DRIPS_not_the_CALLS(db, dripc, client):
    """
    ⚠️ leads.status IS THE CALL STATUS. 'completed' means the dialer finished
    with the lead and says NOTHING about the sequence - a lead can be
    'completed' and mid-drip, which is exactly what it was showing.

    The drip state comes from the same exclusions due() applies, so the roster
    and the sender cannot disagree about where a lead is.
    """
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Done Calling',
                phone_e164='+15553330200', status='completed')
    _join(db, lid, cid, sent_steps=1)
    row = [r for r in drip.roster(cid) if r['lead_id'] == lid][0]
    assert row['status'] == 'completed', 'the call status should be unchanged'
    assert row['drip_state'] in ('waiting', 'sending', 'queued', 'held'), \
        f"a mid-sequence lead reads as {row['drip_state']!r}"

    body = client.get(f'/drips?drip={cid}').text
    assert 'In the sequence' in body, 'the column is still labelled Status'


def test_a_send_time_is_moved_into_the_FIRMS_business_hours(db, dripc, client):
    """
    ⚠️ THE COLUMN MUST ANSWER "WHEN WILL THIS SEND", NOT SHOW THE ARITHMETIC.
    The raw schedule said 1:38am - true, and outside every sending window, so
    nothing was ever going at 1:38am. Same reasoning as showing the call queue's
    real spacing rather than the configured interval.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    cid = dripc['campaign_id']
    # a 09:00-17:00 Mon-Fri week, so a 2am due time cannot be a send time
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE campaign_windows
                              SET enabled = (dow BETWEEN 1 AND 5),
                                  start_time = '09:00', end_time = '17:00'
                            WHERE campaign_id = %s""", (cid,))
    lid = _lead(db, days_ago=11, company='Night Owl', phone_e164='+15553330201')
    _join(db, lid, cid, sent_steps=1)
    row = [r for r in drip.roster(cid) if r['lead_id'] == lid][0]
    assert row['sends_at'] is not None, row

    local = row['sends_at'].astimezone(ZoneInfo(row['timezone']))
    assert 9 <= local.hour < 17, \
        f'the send time is {local:%a %H:%M} local - outside business hours'
    assert local.weekday() < 5, f'the send time is a {local:%A}'
    assert row['sends_at'] >= row['next_due'], \
        'a send cannot be scheduled BEFORE it is due'
    assert row['sends_at_local'], 'no firm-local rendering of the send time'


def test_next_open_reads_the_FIRMS_clock_not_ours(db, dripc):
    """Two leads, same window, timezones a day apart get different instants."""
    import datetime as _dt
    cid = dripc['campaign_id']
    # ⚠️ A NARROW WINDOW, deliberately. The dripc fixture opens all hours so that
    # schedule tests do not depend on the wall clock - and with every hour open
    # any instant is already inside the window, so both timezones would return
    # the same answer and this test would pass without testing anything.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE campaign_windows
                              SET enabled = true, start_time = '09:00',
                                  end_time = '17:00'
                            WHERE campaign_id = %s""", (cid,))
    wins = drip._window_rows(cid)
    # 09:00 UTC is 02:00 in Los Angeles and 05:00 in New York - shut in both, and
    # each opens at 09:00 on its OWN clock, which is a different instant.
    at = _dt.datetime(2026, 9, 15, 9, 0, tzinfo=_dt.timezone.utc)
    la = drip.next_open(at, 'America/Los_Angeles', wins)
    ny = drip.next_open(at, 'America/New_York', wins)
    assert la is not None and ny is not None
    assert la != ny, 'the window was evaluated in one timezone for both firms'


def test_a_lead_on_a_stopped_drip_reads_paused_not_waiting(db, dripc, client):
    cid = dripc['campaign_id']
    lid = _lead(db, days_ago=11, company='Paused Firm', phone_e164='+15553330202')
    _join(db, lid, cid, sent_steps=1)
    campaigns.stop(cid)
    row = [r for r in drip.roster(cid) if r['lead_id'] == lid][0]
    assert row['drip_state'] == 'paused', row
    assert 'drip stopped' in row['state_detail'], row
    assert row['sends_at'] is None, \
        'a paused lead must not advertise a send time it will not honour'


def test_a_held_lead_says_WHICH_cap_is_holding_it(db, dripc, client):
    """"held" without a reason is the same as no answer."""
    cid = dripc['campaign_id']
    open_all_hours(cid)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE campaign_configs SET email_daily_cap = 1,
                                  email_hourly_cap = 1 WHERE campaign_id = %s""",
                        (cid,))
            # one already sent today, so the cap is spent
            lid0 = _lead(db, days_ago=11, phone_e164='+15553330203')
            cur.execute("""INSERT INTO email_sends
                               (lead_id, seq, to_email, sent_at, sent_by,
                                click_token)
                           VALUES (%s, 50, 'a@b.test', now(), 'operator',
                                   'tok-held-cap')""", (lid0,))
    lid = _lead(db, days_ago=11, company='Capped Firm', phone_e164='+15553330204')
    _join(db, lid, cid)
    row = [r for r in drip.roster(cid) if r['lead_id'] == lid][0]
    assert row['drip_state'] == 'held', row
    assert 'cap' in row['state_detail'], row
