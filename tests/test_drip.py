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
    """Two steps due the same day means two emails at once."""
    c = campaigns.create('DRIP-D', campaign_type='drip')['campaign_id']
    with pytest.raises(drip.BadSequence):
        drip.save_steps(c, [{'delay_days': 4, 'subject': 'a', 'body': 'a'},
                            {'delay_days': 4, 'subject': 'b', 'body': 'b'}])


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
