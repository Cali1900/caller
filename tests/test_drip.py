"""
THE DRIP.

Two anchors, and every test here is about one of them:

  THE SCHEDULE anchors to leads.emailed_at - the FIRST send. Every step's
  delay_days is measured from there, never from the step before it.

  CLICK TIMING anchors to the step's OWN send, so "47m after send" on step 3
  means 47 minutes after step 3 went out.

⚠️ THE REPLY GATE IS NOT FAIL-CLOSED and cannot be: detection is manual. These
tests pin that replied_at STOPS the sequence once recorded - not that a reply
which has not been ticked stops anything, because nothing in code can know.
"""
import datetime

import pytest

from api import (archive, autosend, campaigns, clicks, db as dbm, drip,
                 stages)
from tests.conftest import running_campaign_id

LA = 'America/Los_Angeles'


@pytest.fixture
def dripc(db):
    """A running drip campaign with a four-step sequence: 0, 4, 10, 21 days."""
    c = campaigns.create('DRIP-T', campaign_type='drip')
    cid = c['campaign_id']
    campaigns.start(cid)
    drip.save_steps(cid, [
        {'delay_days': 0,  'subject': 'Following up', 'body': 'One {{first_name}} {{sample_link}}'},
        {'delay_days': 4,  'subject': 'Second',       'body': 'Two {{sample_link}}'},
        {'delay_days': 10, 'subject': 'Third',        'body': 'Three {{sample_link}}'},
        {'delay_days': 21, 'subject': 'Last',         'body': 'Four {{sample_link}}'},
    ])
    return campaigns.get(cid)


def _lead(db, days_ago=0, **kw):
    cols = {'company': 'Whitfield Law', 'phone_e164': '+15553330001',
            'timezone': LA, 'pool_status': 'active', 'status': 'emailed',
            'has_confirmed_email': True, 'dm_email': 'pat@whitfield.test',
            'dm_email_confirmed': True, 'dm_name': 'Pat Kelly',
            'website': 'whitfield.test', 'campaign_id': running_campaign_id()}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
        cur.execute("""UPDATE leads
                          SET emailed_at = now() - (%s || ' days')::interval,
                              emailed_by = 'operator'
                        WHERE lead_id = %s""", (days_ago, lid))
    db.commit()
    return lid


def _get_lead(db, lid):
    with db.cursor() as cur:
        cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lid,))
        return cur.fetchone()


def _join(db, lid, cid, sent_steps=0):
    """Put the lead on the drip and mark the first `sent_steps` steps as sent."""
    steps = drip.steps(cid)
    with db.cursor() as cur:
        cur.execute('UPDATE leads SET drip_campaign_id=%s WHERE lead_id=%s',
                    (cid, lid))
        for i, st in enumerate(steps[:sent_steps], start=1):
            # WITH A TOKEN. A send row without one cannot be clicked, and a
            # click test whose click silently does not happen passes for the
            # wrong reason - which is exactly how break 100 caught this.
            cur.execute("""INSERT INTO email_sends
                               (lead_id, step_id, seq, to_email, sent_at,
                                click_token)
                           VALUES (%s,%s,%s,'pat@whitfield.test', now(), %s)""",
                        (lid, st['step_id'], i, f'tok-{lid}-{i}'))
    db.commit()


# ==========================================================================
# the sequence is Sean's, not the schema's
# ==========================================================================

def test_any_number_of_steps(db):
    c = campaigns.create('DRIP-N', campaign_type='drip')['campaign_id']
    for n in (1, 3, 7):
        rows = [{'delay_days': i * 3, 'subject': f's{i}', 'body': f'b{i}'}
                for i in range(n)]
        assert len(drip.save_steps(c, rows)) == n
        assert len(drip.steps(c)) == n, 'the sequence length is not the schema\'s'


def test_an_empty_sequence_is_refused(db):
    """A drip with no steps looks like it sends and does not."""
    c = campaigns.create('DRIP-E', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence):
        drip.save_steps(c, [])


def test_a_backwards_delay_is_refused(db):
    """Step 3 falling due before step 2 sends the sequence out of order."""
    c = campaigns.create('DRIP-B', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence) as e:
        drip.save_steps(c, [{'delay_days': 0, 'subject': 'a', 'body': 'a'},
                            {'delay_days': 10, 'subject': 'b', 'body': 'b'},
                            {'delay_days': 4, 'subject': 'c', 'body': 'c'}])
    assert 'BEFORE' in str(e.value)


def test_a_duplicate_delay_is_refused(db):
    """
    Two steps due the same day means two emails at once.

    Needs THREE steps now: step 1 is timed in MINUTES from entering the drip, not
    in days, so it is not on the scale this rule compares - it is the first send
    and cannot be N days from itself. The duplicate has to be between two LATER
    steps.
    """
    c = campaigns.create('DRIP-D', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence) as e:
        drip.save_steps(c, [{'delay_minutes': 0, 'subject': 'a', 'body': 'a'},
                            {'delay_days': 4, 'subject': 'b', 'body': 'b'},
                            {'delay_days': 4, 'subject': 'c', 'body': 'c'}])
    assert 'same day' in str(e.value)


def test_a_later_step_may_not_land_on_day_zero(db):
    """Day 0 is step 1. A second email in the same minute reads as a
    malfunction, and is the fastest way to get a sending domain blocked."""
    c = campaigns.create('DRIP-Z', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence) as e:
        drip.save_steps(c, [{'delay_minutes': 0, 'subject': 'a', 'body': 'a'},
                            {'delay_days': 0, 'subject': 'b', 'body': 'b'}])
    assert 'at least a day' in str(e.value)


def test_a_step_with_no_body_is_refused(db):
    c = campaigns.create('DRIP-X', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence):
        drip.save_steps(c, [{'delay_days': 0, 'subject': 'a', 'body': '   '}])


# ==========================================================================
# DELAYS ANCHOR TO emailed_at, NEVER TO THE PREVIOUS STEP
# ==========================================================================

def test_delays_anchor_to_the_first_send_not_the_previous_step(db, dripc):
    """
    ⚠️ THE CORE SCHEDULING RULE.

    Steps are at days 0, 4, 10, 21 from emailed_at. A lead emailed 11 days ago
    with steps 1 and 2 already sent is due STEP 3 (day 10) - not step 3 counted
    four days after step 2 went out.

    Chaining would make the sequence drift by however long each send was late;
    anchoring cannot drift at all.
    """
    lid = _lead(db, days_ago=11)
    _join(db, lid, dripc['campaign_id'], sent_steps=2)
    rows = drip.due()
    mine = [r for r in rows if str(r['lead_id']) == str(lid)]
    assert len(mine) == 1
    assert mine[0]['position'] == 3, 'the schedule did not anchor to emailed_at'
    assert mine[0]['delay_days'] == 10


def test_a_step_not_yet_due_is_not_selected(db, dripc):
    lid = _lead(db, days_ago=5)
    _join(db, lid, dripc['campaign_id'], sent_steps=2)
    # day 5: step 2 (day 4) is sent, step 3 (day 10) is five days away.
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)]


def test_only_the_earliest_unsent_due_step_is_selected(db, dripc):
    """
    After a long pause two steps can be due at once. Sending both would put two
    emails in front of a firm in one minute; the sequence must advance one step
    at a time.
    """
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert len(mine) == 1, 'more than one step was selected for one lead'
    assert mine[0]['position'] == 2, 'it must advance in order'


# ==========================================================================
# a step already sent is never re-sent, whatever happens to the sequence
# ==========================================================================

def test_editing_copy_does_not_resend_a_step(db, dripc):
    lid = _lead(db, days_ago=40)
    cid = dripc['campaign_id']
    _join(db, lid, cid, sent_steps=2)
    steps = drip.steps(cid)
    drip.save_steps(cid, [
        {'step_id': steps[0]['step_id'], 'delay_days': 0, 'subject': 'EDITED', 'body': 'new'},
        {'step_id': steps[1]['step_id'], 'delay_days': 4, 'subject': 'EDITED2', 'body': 'new'},
        {'step_id': steps[2]['step_id'], 'delay_days': 10, 'subject': 'Third', 'body': 'Three'},
        {'step_id': steps[3]['step_id'], 'delay_days': 21, 'subject': 'Last', 'body': 'Four'},
    ])
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert [r['position'] for r in mine] == [3], \
        'editing a sent step made it due again'


def test_deleting_a_step_keeps_the_record_of_who_got_it(db, dripc):
    """Those who got it keep the record; others skip it. A hard delete would
    cascade the send rows away and silently renumber what everyone received."""
    lid = _lead(db, days_ago=40)
    cid = dripc['campaign_id']
    _join(db, lid, cid, sent_steps=2)
    steps = drip.steps(cid)
    drip.save_steps(cid, [
        {'step_id': steps[0]['step_id'], 'delay_days': 0, 'subject': 'a', 'body': 'a'},
        {'step_id': steps[2]['step_id'], 'delay_days': 10, 'subject': 'c', 'body': 'c'},
    ])
    assert len(drip.sends(lid)) == 2, 'the send records were destroyed'
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT deleted_at FROM drip_steps WHERE step_id = %s',
                        (steps[1]['step_id'],))
            assert cur.fetchone()['deleted_at'] is not None, 'not soft-deleted'


def test_inserting_a_step_does_not_send_backwards(db, dripc):
    """A lead past that position must not go back for a newly inserted step."""
    lid = _lead(db, days_ago=40)
    cid = dripc['campaign_id']
    _join(db, lid, cid, sent_steps=3)
    steps = drip.steps(cid)
    drip.save_steps(cid, [
        {'step_id': steps[0]['step_id'], 'delay_days': 0, 'subject': 'a', 'body': 'a'},
        {'step_id': steps[1]['step_id'], 'delay_days': 4, 'subject': 'b', 'body': 'b'},
        {'delay_days': 7, 'subject': 'INSERTED', 'body': 'new'},
        {'step_id': steps[2]['step_id'], 'delay_days': 10, 'subject': 'c', 'body': 'c'},
        {'step_id': steps[3]['step_id'], 'delay_days': 21, 'subject': 'd', 'body': 'd'},
    ])
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    # The inserted step at day 7 is unsent and due - but the lead already had
    # three steps, so the SENT ones must not reappear.
    assert all(r['subject'] != 'a' and r['subject'] != 'b' for r in mine)


# ==========================================================================
# what stops it
# ==========================================================================

def test_a_recorded_reply_stops_the_sequence(db, dripc):
    """The gate. Once replied_at is set, nothing more goes out."""
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], 'premise'
    stages.record_reply(lid, note='not interested')
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'a lead that replied is still being dripped'


def test_a_click_does_NOT_stop_the_sequence(db, dripc):
    """
    A click is interest, not an answer. Stopping on one would silence the
    sequence exactly when it is working. ONLY A REPLY STOPS IT.
    """
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT click_token FROM email_sends
                            WHERE lead_id = %s AND click_token IS NOT NULL
                            LIMIT 1""", (lid,))
            row = cur.fetchone()
    assert row is not None, 'the fixture produced no clickable send'
    assert clicks.record(row['click_token'], user_agent='t') is not None, \
        'the click was not recorded'

    # ⚠️ PROVE THE PREMISE. Without this the test passes when NO click happened,
    # which is exactly how it passed before break 100 reported GREEN: _join was
    # inserting send rows with no token, clicks.record() returned None, and
    # "a click does not stop the drip" was asserting nothing at all.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) AS n FROM email_clicks WHERE lead_id = %s',
                        (lid,))
            assert cur.fetchone()['n'] == 1, 'no click was recorded to test with'

    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'a click stopped the drip - only a reply may do that'


def test_a_stopped_drip_campaign_sends_nothing(db, dripc):
    """is_running is the switch, exactly as it is for a call campaign."""
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], 'premise'
    campaigns.stop(dripc['campaign_id'])
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)]


def test_an_archived_lead_gets_no_steps(db, dripc):
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    archive.archive(lid, 'manual')
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)]


def test_the_do_not_send_list_stops_every_step(db, dripc):
    """Keyed on the ADDRESS, so it outlives the lead."""
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    archive.do_not_send('pat@whitfield.test', 'unsubscribed', 'test')
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)]


def test_an_unsubscribe_stops_email_and_leaves_the_phone_alone(db, dripc):
    """
    Someone who does not want our emails has not given up the right to be
    phoned about a case they asked about. This must NEVER write to suppression.
    """
    lid = _lead(db, days_ago=40, phone_e164='+15553330099')
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    drip.stop(lid, 'unsubscribed', by='test')
    assert archive.is_do_not_send('pat@whitfield.test')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM suppression "
                        "WHERE phone_e164 = '+15553330099'")
            assert cur.fetchone()['n'] == 0, \
                'an email unsubscribe suppressed the PHONE - it must not'


def test_every_stop_records_why(db, dripc):
    lid = _lead(db, days_ago=40)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    drip.stop(lid, 'demo_booked', by='sean')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT summary, detail FROM activity
                            WHERE lead_id = %s AND kind = 'drip'
                            ORDER BY created_at DESC LIMIT 1""", (lid,))
            row = cur.fetchone()
    assert 'demo_booked' in row['summary'] and 'sean' in row['detail']


def test_an_unknown_stop_reason_is_refused(db, dripc):
    lid = _lead(db, days_ago=1)
    with pytest.raises(ValueError):
        drip.stop(lid, 'because', by='test')


# ==========================================================================
# the screens render WITH DATA
#
# A 200 on a page with no drip proves nothing about the branches that only
# appear once there is one. Templates fail at RENDER time, so an untested branch
# is a 500 in front of Sean the first time he uses the feature.
# ==========================================================================

@pytest.fixture
def client(db, cfg_env):
    from fastapi.testclient import TestClient
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def test_the_sequence_editor_renders_for_a_drip_campaign(db, dripc, client):
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert r.status_code == 200, r.text[:400]
    assert 'The sequence' in r.text
    # Every saved step is editable, and the preview is rendered.
    for st in drip.steps(dripc['campaign_id']):
        assert f'value="{st["delay_days"]}"' in r.text, \
            f'step at day {st["delay_days"]} has no delay input'
    assert 'Save the sequence' in r.text
    assert 'after the last step' in r.text.lower()


def test_the_editor_says_a_call_campaign_has_no_sequence(db, client):
    """Rendering the editor on a call campaign must explain, not offer a form
    that cannot work."""
    r = client.get(f'/campaign/{running_campaign_id()}')
    assert r.status_code == 200, r.text[:400]
    assert 'drip campaigns only' in r.text
    assert 'Save the sequence' not in r.text


def test_saving_a_bad_sequence_is_refused_on_the_screen(db, dripc, client):
    """The refusal has to reach the person, not just the log."""
    steps = drip.steps(dripc['campaign_id'])
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps", data={
        'step_id_0': str(steps[0]['step_id']), 'delay_0': '0',
        'subject_0': 'a', 'body_0': 'a',
        'step_id_1': str(steps[1]['step_id']), 'delay_1': '0',
        'subject_1': 'b', 'body_1': 'b',
    }, follow_redirects=False)
    assert 'REJECTED' in r.headers['location']
    # And nothing was half-saved.
    assert len(drip.steps(dripc['campaign_id'])) == 4


def test_the_lead_page_renders_the_drip_and_the_send_history(db, dripc, client):
    lid = _lead(db, days_ago=11)
    _join(db, lid, dripc['campaign_id'], sent_steps=2)
    r = client.get(f'/leads/{lid}')
    assert r.status_code == 200, r.text[:400]
    assert 'The drip' in r.text
    assert dripc['name'] in r.text
    assert 'Emails sent' in r.text
    assert 'Stop the drip for this lead' in r.text, \
        'the control the digest checkpoint points at is missing'


def test_the_lead_page_renders_the_archived_contact_history(db, dripc, client):
    """
    ⚠️ THE WHOLE POINT OF archived_contacts IS THAT SOMEBODY CAN SEE IT.
    It had a reader and tests and no template until 2026-09-09 - data that
    survives a return and cannot be read is the same fault as an inventory
    nobody updates.
    """
    lid = _lead(db, days_ago=200)
    stages.record_reply(lid, note='not interested this quarter', by='sean')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='new' WHERE lead_id=%s", (lid,))
    archive.archive(lid, 'no_reply', by='test')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET returns_at = now() - interval '1 day' "
                        "WHERE lead_id = %s", (lid,))
    assert archive.return_due()['returned'] == 1

    r = client.get(f'/leads/{lid}')
    assert r.status_code == 200, r.text[:400]
    assert 'Before it was returned' in r.text
    assert 'no_reply' in r.text
    assert 'not interested this quarter' in r.text, \
        'the reply that was cleared as a GATE must still be readable as a FACT'


def test_the_digest_names_tomorrows_sends_by_firm(db, dripc, cfg_env):
    """
    THE CHECKPOINT FOR A GATE THAT CANNOT FAIL CLOSED.

    Reply detection is manual, so this block is what makes the race visible: a
    COUNT is not actionable, a firm name is. It must also name the control -
    stopping one lead rather than pausing the drip.
    """
    from api import digest
    lid = _lead(db, days_ago=11, company='Whitfield Law')
    _join(db, lid, dripc['campaign_id'], sent_steps=2)
    body = digest.build(cfg_env)['body']
    assert 'GOING OUT IN THE NEXT 24 HOURS' in body
    assert 'Whitfield Law' in body, 'a count is not actionable; a firm name is'
    assert 'step 3' in body
    assert 'stop' in body.lower()


def test_a_bounce_is_visible_in_the_status_filter(db, dripc, client):
    """
    A BOUNCE MUST SHOW, not just stop.

    Putting the address on the do-not-send list is what prevents another send.
    It is not what tells anyone it happened. Without the status change the lead
    sits at 'emailed' with no drip and no explanation - in no filter, on no
    queue, simply stopped. A lead failing silently is the shape every other guard
    in this system exists to prevent.

    ASSERTED THROUGH THE REAL FILTER, not just the column: "it shows up" is the
    property, and a status nothing can filter on would satisfy the column check
    while failing the point.
    """
    lid = _lead(db, days_ago=11, company='Bouncy Law LLP')
    _join(db, lid, dripc['campaign_id'], sent_steps=1)

    assert drip.stop(lid, 'bounced', by='test') is True

    row = _get_lead(db, lid)
    assert row['status'] == 'bad_email', 'the bounce left no visible trace'
    assert row['drip_campaign_id'] is None, 'the drip did not stop'
    # The ADDRESS is blocked, which is the half that stops another send.
    assert archive.is_do_not_send('pat@whitfield.test')
    # NOT archived: a bounce wants a corrected address, from a person.
    assert row['archived_at'] is None, \
        'a bounce archived the lead - it should be left for a person'

    r = client.get('/?status=bad_email')
    assert r.status_code == 200
    assert 'Bouncy Law LLP' in r.text, \
        'the bounced lead does not appear when filtering on bad_email'


def test_a_bounce_records_why_on_the_timeline(db, dripc):
    lid = _lead(db, days_ago=11)
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    drip.stop(lid, 'bounced', by='sean')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT summary, detail FROM activity
                            WHERE lead_id = %s AND kind = 'drip'
                            ORDER BY created_at""", (lid,))
            rows = cur.fetchall()
    trail = ' | '.join(f"{r['summary']} :: {r['detail'] or ''}" for r in rows)
    assert 'bad_email' in trail and 'bounced' in trail
    assert 'corrected address' in trail, \
        'the timeline must say WHY it was not archived'
    # The bound detail must not carry SQL indentation into the timeline.
    for r in rows:
        if r['detail']:
            assert '\n' not in r['detail'], 'a newline leaked into the detail text'


# ==========================================================================
# THE DRIP IS REACHABLE FROM THE UI
#
# It was built and unreachable: the New Campaign form made call campaigns only,
# with no type selector, so a drip could not be created without SQL. Feature
# built and not wired is the same fault as a control that exists and cannot be
# found - the archive Restore button, inverted.
# ==========================================================================

def test_the_create_form_can_make_a_drip(db, client):
    r = client.post('/campaigns/new',
                    data={'name': 'D-UI', 'campaign_type': 'drip',
                          'template_from': '', 'notes': 'from the form'},
                    follow_redirects=False)
    assert r.status_code == 303
    made = [c for c in campaigns.list_all() if c['name'] == 'D-UI']
    assert len(made) == 1
    assert made[0]['type'] == 'drip', 'the form made a CALL campaign'
    assert made[0]['is_running'] is False, 'created stopped, always'


def test_the_create_form_still_defaults_to_call(db, client):
    """Anything posting without the field must behave as it did before."""
    r = client.post('/campaigns/new',
                    data={'name': 'C-UI', 'template_from': '', 'notes': ''},
                    follow_redirects=False)
    assert r.status_code == 303
    made = [c for c in campaigns.list_all() if c['name'] == 'C-UI']
    assert made[0]['type'] == 'call'


def test_a_bad_type_is_refused_not_silently_a_call_campaign(db, client):
    r = client.post('/campaigns/new',
                    data={'name': 'X-UI', 'campaign_type': 'email',
                          'template_from': '', 'notes': ''},
                    follow_redirects=False)
    assert 'could+not+create' in r.headers['location'] \
        or 'could%20not%20create' in r.headers['location']
    assert not [c for c in campaigns.list_all() if c['name'] == 'X-UI'], \
        'a bad type created a campaign anyway'


def test_the_campaigns_page_labels_and_groups_by_type(db, dripc, client):
    r = client.get('/campaigns')
    assert r.status_code == 200, r.text[:400]
    assert 'Call campaigns' in r.text and 'Drip campaigns' in r.text
    assert 'many run at once' in r.text
    assert 'campaign_type' in r.text, 'the create form has no type selector'
    # ⚠️ THE HEADER MUST NOT CLAIM ONE-AT-A-TIME GLOBALLY. It is true of call
    # campaigns and false of drips, and the index enforcing it is scoped to
    # type='call'.
    assert '<b>One runs at a time.</b>' not in r.text, \
        'the page still says one campaign runs at a time, which is only true of calls'


def test_the_list_warns_about_a_drip_with_no_steps(db, client):
    """A drip with no sequence sends nothing, and that must be visible from the
    list rather than only after opening it."""
    cid = campaigns.create('D-EMPTY', campaign_type='drip')['campaign_id']
    campaigns.start(cid)
    r = client.get('/campaigns')
    assert 'no steps yet' in r.text


def test_a_drip_page_hides_what_a_drip_does_not_have(db, dripc, client):
    """
    Prompt version, cap, spacing and calling windows belong to the CALL campaign
    and it keeps them. A blank box on a drip reads as "not configured yet"
    rather than "does not apply", which is how somebody ends up trying to set a
    dial interval on an email sequence.
    """
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert r.status_code == 200, r.text[:400]
    assert 'The sequence' in r.text and 'Save the sequence' in r.text
    # MATCHED ON THE HEADING, not the bare phrase. base.html carries a CSS
    # comment reading "Prompt versions: seventeen and growing", so a substring
    # check passes on every page in the app and this test would have asserted
    # nothing about the drip page at all.
    for gone in ('Prompt version', 'Daily cap', 'Spacing', 'Calling window',
                 'Retry gaps'):
        assert f'<h2>{gone}</h2>' not in r.text, \
            f'a drip page still shows the {gone!r} section'
    # It does own a sender, and it says what it lacks and why.
    assert '<h2>Sender</h2>' in r.text
    assert 'does not dial' in r.text


def test_a_call_page_is_unchanged(db, client):
    """The other half of the same change: nothing was taken off a call
    campaign's page."""
    r = client.get(f'/campaign/{running_campaign_id()}')
    assert r.status_code == 200, r.text[:400]
    for kept in ('Prompt version', 'Daily cap', 'Spacing', 'Calling window',
                 'Retry gaps', 'Follow-up email copy', 'Sender'):
        assert f'<h2>{kept}</h2>' in r.text, f'a call page lost {kept!r}'
    assert 'Save the sequence' not in r.text, \
        'a call campaign was offered a sequence editor it cannot use'


# ==========================================================================
# THE SEQUENCE EDITOR
#
# Every one of these guards something that can be DECORATION: a toggle the
# sender ignores, a delay that does not delay, a preview that disagrees with
# what goes out. A control the screen promises and the code does not honour is
# worse than no control.
# ==========================================================================

def test_a_disabled_step_is_never_sent(db, dripc):
    """
    ⚠️ THE TOGGLE MUST NOT BE DECORATION. The screen says the step is off; if the
    sender mails it anyway that is worse than having no toggle, because the
    operator has been TOLD it will not happen.
    """
    lid = _lead(db, days_ago=40)
    cid = dripc['campaign_id']
    _join(db, lid, cid, sent_steps=1)
    steps = drip.steps(cid)

    # Premise: step 2 is due.
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert [r['position'] for r in mine] == [2], 'premise'

    drip.save_steps(cid, [
        {'step_id': steps[0]['step_id'], 'delay_minutes': 0,
         'subject': 'a', 'body': 'a', 'enabled': '1'},
        {'step_id': steps[1]['step_id'], 'delay_days': 4,
         'subject': 'b', 'body': 'b', 'enabled': ''},        # OFF
        {'step_id': steps[2]['step_id'], 'delay_days': 10,
         'subject': 'c', 'body': 'c', 'enabled': '1'},
        {'step_id': steps[3]['step_id'], 'delay_days': 21,
         'subject': 'd', 'body': 'd', 'enabled': '1'},
    ])
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert [r['position'] for r in mine] == [3], \
        'a disabled step was still selected, or it blocked the sequence'


def test_a_disabled_step_keeps_its_copy_and_its_sends(db, dripc):
    """Disabling is not deleting. Re-enabling must restore the same step, and a
    lead that already had it must not receive it twice."""
    lid = _lead(db, days_ago=40)
    cid = dripc['campaign_id']
    _join(db, lid, cid, sent_steps=2)
    steps = drip.steps(cid)
    rows = [{'step_id': s['step_id'],
             'delay_minutes': 0 if s['position'] == 1 else None,
             'delay_days': s['delay_days'],
             'subject': s['subject'], 'body': s['body'],
             'enabled': '' if s['position'] == 2 else '1'} for s in steps]
    drip.save_steps(cid, rows)
    off = [s for s in drip.steps(cid) if s['position'] == 2][0]
    assert off['enabled'] is False
    assert off['subject'] == 'Second', 'the copy was lost'
    assert off['deleted_at'] is None, 'disabling soft-deleted it'
    assert len(drip.sends(lid)) == 2, 'the send records were lost'

    # Re-enable: still not re-sent, because the send record stands.
    for r in rows:
        r['enabled'] = '1'
    drip.save_steps(cid, rows)
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert 2 not in [r['position'] for r in mine], \
        're-enabling a step re-sent it to a lead that already had it'


def test_the_ordering_check_spans_disabled_steps(db):
    """
    A disabled step keeps its delay, and re-enabling it is a single checkbox with
    no validation of its own. So a sequence that would be BACKWARDS once
    re-enabled must be refused at save time, not at re-enable time.
    """
    c = campaigns.create('DRIP-ORD', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence):
        drip.save_steps(c, [
            {'delay_minutes': 0, 'subject': 'a', 'body': 'a', 'enabled': '1'},
            {'delay_days': 10, 'subject': 'b', 'body': 'b', 'enabled': ''},
            {'delay_days': 4, 'subject': 'c', 'body': 'c', 'enabled': '1'},
        ])


def test_step_1_is_timed_in_minutes_from_entering_the_drip(db, dripc):
    """
    STEP 1 IS THE FIRST SEND, so it cannot be N days from itself. Its clock runs
    from leads.drip_entered_at, which is why that column exists.
    """
    cid = dripc['campaign_id']
    steps = drip.steps(cid)
    assert steps[0]['delay_minutes'] is not None, 'step 1 has no minute timing'
    assert steps[0]['delay_days'] == 0, 'step 1 must not be on the day scale'
    for s in steps[1:]:
        assert s['delay_minutes'] is None, 'a later step got minute timing'
        assert s['delay_days'] >= 1


def test_step_1_waits_for_its_delay_before_sending(db, dripc):
    """
    ⚠️ A DELAY THAT DOES NOT DELAY IS THE WORST KIND OF CONTROL.

    "after 15 minutes" sending instantly means an imported batch of 500 firms is
    mailed the second it is uploaded - and upload is the moment a mistake is most
    likely. Those minutes are the window in which it can still be stopped.
    """
    cid = dripc['campaign_id']
    steps = drip.steps(cid)
    drip.save_steps(cid, [
        {'step_id': steps[0]['step_id'], 'delay_minutes': 30,
         'subject': 'a', 'body': 'a', 'enabled': '1'},
    ] + [{'step_id': s['step_id'], 'delay_days': s['delay_days'],
          'subject': s['subject'], 'body': s['body'], 'enabled': '1'}
         for s in steps[1:]])

    # An imported lead: on the drip, no emailed_at, entered just now.
    lid = _lead(db, days_ago=0, phone_e164='+15553339001',
                lead_source='import')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads SET emailed_at = NULL, emailed_by = NULL,
                                  drip_campaign_id = %s, drip_entered_at = now()
                            WHERE lead_id = %s""", (cid, lid))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'step 1 sent immediately despite a 30-minute delay'

    # Move the DATA, not the clock.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads
                              SET drip_entered_at = now() - interval '31 minutes'
                            WHERE lead_id = %s""", (lid,))
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert [r['position'] for r in mine] == [1], 'step 1 never became due'


def test_entering_a_drip_stamps_when(db, dripc):
    """Step 1's delay has nothing to count from otherwise."""
    lid = _lead(db, days_ago=0, phone_e164='+15553339002')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            assert drip.enter(cur, lid, dripc['campaign_id']) is True
    assert _get_lead(db, lid)['drip_entered_at'] is not None


def test_stopping_a_drip_clears_the_entry_time(db, dripc):
    """If this lead is ever put on a drip again, step 1 must be timed from THAT
    entry, not the old one."""
    lid = _lead(db, days_ago=0, phone_e164='+15553339003')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            drip.enter(cur, lid, dripc['campaign_id'])
    drip.stop(lid, 'by_hand', by='test')
    row = _get_lead(db, lid)
    assert row['drip_campaign_id'] is None
    assert row['drip_entered_at'] is None


def test_a_disabled_tail_step_does_not_block_finishing(db, dripc):
    """
    Otherwise the sequence never terminates and the lead sits in the drip
    forever - the limbo the whole model refuses.
    """
    cid = dripc['campaign_id']
    steps = drip.steps(cid)
    rows = [{'step_id': s['step_id'],
             'delay_minutes': 0 if s['position'] == 1 else None,
             'delay_days': s['delay_days'], 'subject': s['subject'],
             'body': s['body'],
             'enabled': '' if s['position'] == 4 else '1'} for s in steps]
    drip.save_steps(cid, rows)

    lid = _lead(db, days_ago=40, phone_e164='+15553339004')
    _join(db, lid, cid, sent_steps=3)
    assert drip._maybe_finish(None, lid) is True, \
        'three of three ENABLED steps sent, and it did not finish'
    assert _get_lead(db, lid)['status'] == 'archived'


def test_the_preview_uses_the_same_render_the_real_sends_use(db, dripc, client):
    """
    ⚠️ THE WHOLE VALUE OF THE PREVIEW IS THAT IT CANNOT DRIFT.

    A client-side preview would be a second implementation of the substitution
    rules, and the first time the two disagreed it would be LYING about what goes
    out - worse than no preview, because it would be trusted. So it posts to the
    server and comes back through drafts.render() / values_for().
    """
    from api import drafts
    lead = _lead(db, days_ago=1, phone_e164='+15553339010',
                 company='Whitfield Law', dm_name='Timothy Ross')
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                    data={'subject': 'Following up, {{first_name}}',
                          'body': 'Hi {{first_name}} at {{company}}.',
                          'lead_id': str(lead)})
    assert r.status_code == 200
    j = r.json()
    assert j['subject'] == 'Following up, Timothy'
    assert 'Hi Timothy at Whitfield Law.' == j['body']

    # Byte-identical to what the real path produces for the same input.
    from api import campaigns as _c
    camp = _c.get(dripc['campaign_id'])
    lead_row = drafts.lead_for_preview(lead)
    vals = drafts.values_for(lead_row, camp)
    assert j['body'] == drafts.render('Hi {{first_name}} at {{company}}.', vals)


def test_the_preview_names_an_unresolved_placeholder(db, dripc, client):
    """
    A typo renders as ITSELF and is easy to miss in prose - the real send would
    post {{frist_name}} to a law firm verbatim. That is the failure the
    placeholder menu removes and this catches when somebody types anyway.
    """
    lead = _lead(db, days_ago=1, phone_e164='+15553339011')
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                    data={'subject': 'Hi {{frist_name}}', 'body': 'x',
                          'lead_id': str(lead)})
    j = r.json()
    assert 'frist_name' in j['unknown']
    assert j['subject'] == 'Hi {{frist_name}}', \
        'an unknown placeholder must render as itself, not be blanked - blanking '\
        'hides the typo instead of showing it'


def test_the_preview_reports_a_body_count(db, dripc, client):
    lead = _lead(db, days_ago=1, phone_e164='+15553339012')
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                    data={'subject': 's', 'body': 'one two three',
                          'lead_id': str(lead)})
    j = r.json()
    assert j['words'] == 3
    assert j['chars'] == len('one two three')


def test_the_editor_offers_real_leads_and_every_placeholder(db, dripc, client):
    _lead(db, days_ago=1, phone_e164='+15553339013', company='Bergman & Co',
          dm_name='Ada Bergman')
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert r.status_code == 200, r.text[:400]
    # A real lead to render against, by name - not {{first_name}}.
    assert 'Bergman &amp; Co' in r.text or 'Bergman & Co' in r.text
    assert 'pv-lead' in r.text, 'no preview-lead dropdown'
    # Every placeholder is offered, so none has to be typed from memory.
    from api import drafts
    for ph in drafts.PLACEHOLDERS:
        assert ph in r.text, f'{ph} is not offered in the placeholder menu'


def test_the_editor_reads_top_to_bottom_and_gives_the_body_room(db, dripc, client):
    """
    SUBJECT ABOVE BODY, the way an email reads - it used to sit below, which read
    backwards. And the body needs real height: four lines is too small to judge
    copy on.
    """
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    body_at = r.text.index('name="body_0"')
    subj_at = r.text.index('name="subject_0"')
    assert subj_at < body_at, 'the subject renders below the body'
    import re
    rows = re.search(r'id="bod0"[^>]*rows="(\d+)"', r.text)
    assert rows and int(rows.group(1)) >= 12, \
        'the body box is too short to judge copy in'


def test_each_step_collapses_and_the_delay_sits_between_them(db, dripc, client):
    """A four-step sequence has to fit on screen, and the delay is a property of
    the GAP rather than of the email - reading "send in 8 days" between two cards
    is how the sequence's rhythm becomes visible."""
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert '<details' in r.text, 'steps do not collapse'
    assert 'days after the <b>first</b> send' in r.text, \
        'the delay is not shown between the steps'


def test_step_1_is_not_offered_a_day_field(db, dripc, client):
    """It IS the first send. A day field on it read as a control and was not one."""
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert 'name="minutes_0"' in r.text, 'step 1 has no minute timing control'
    assert 'name="delay_0"' not in r.text, \
        'step 1 is still offered a day field it cannot honour'
    assert 'imported' in r.text.lower(), \
        'the screen must say step 1 timing governs imported leads only'


def test_a_step_created_without_the_field_is_enabled(db):
    """
    ⚠️ ISOLATES THE DEFAULT, which nothing else does: every other test here
    passes `enabled` explicitly, so drip._flag() never sees None and the default
    is never exercised.

    An unchecked checkbox posts nothing, so from a FORM absent means off - the web
    handler therefore always supplies the key. A programmatic caller (a test, a
    seed, a script) passes rows without it and means "a normal enabled step".
    Collapsing those two silently disabled every step created outside the form,
    and a seeded sequence that quietly never sends is the unlucky version of that.

    Third time this shape has bitten: an absent retry ladder is not an empty one,
    an absent campaign type is not 'call', and now this.
    """
    c = campaigns.create('DRIP-DEF', campaign_type='drip')['campaign_id']
    saved = drip.save_steps(c, [
        {'delay_minutes': 0, 'subject': 'a', 'body': 'a'},      # no 'enabled'
        {'delay_days': 4, 'subject': 'b', 'body': 'b'},         # no 'enabled'
    ])
    assert all(s['enabled'] is True for s in saved), \
        'a step created without the field came out DISABLED - it would never send'
    assert all(s['enabled'] is True for s in drip.steps(c))

    # And the form's explicit 'off' still works, or the default would be
    # overriding a real choice.
    steps = drip.steps(c)
    drip.save_steps(c, [
        {'step_id': steps[0]['step_id'], 'delay_minutes': 0,
         'subject': 'a', 'body': 'a', 'enabled': '1'},
        {'step_id': steps[1]['step_id'], 'delay_days': 4,
         'subject': 'b', 'body': 'b', 'enabled': ''},
    ])
    assert [s['enabled'] for s in drip.steps(c)] == [True, False]
