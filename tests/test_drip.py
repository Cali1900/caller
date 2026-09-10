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


def open_all_hours(cid):
    """
    Business hours 00:00-23:59 every day, for tests that are about the SCHEDULE.

    ⚠️ WITHOUT THIS EVERY DRIP TEST DEPENDS ON THE WALL CLOCK. A campaign is
    created with Mon-Fri 09:00-17:00 and Sunday off, and drip.due() now reads
    those windows in the lead's timezone - so a suite run at 18:00 LA or on a
    Sunday would see zero due leads and fail everywhere, for a reason that has
    nothing to do with what any of those tests assert.

    The business-hours behaviour gets its OWN tests, which set a narrow window
    deliberately. Same rule as everything else here: a guard is only tested if
    the test isolates it.
    """
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE campaign_windows
                              SET enabled = true, start_time = '00:00',
                                  end_time = '23:59'
                            WHERE campaign_id = %s""", (cid,))


@pytest.fixture
def dripc(db):
    """A running drip campaign with a four-step sequence: 0, 4, 10, 21 days."""
    c = campaigns.create('DRIP-T', campaign_type='drip')
    cid = c['campaign_id']
    campaigns.start(cid)
    open_all_hours(cid)
    # ⚠️ THE GATE IS THE MEMBERSHIP. A drip with no accepted_statuses accepts
    # nobody, so a fixture that forgot this would test a drip that can never
    # send - and every assertion about selection would pass for the wrong reason.
    campaigns.update(cid, accepted_statuses=['emailed'])
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
    """
    Make the lead QUALIFY for the drip, and mark the first `sent_steps` sent.

    ⚠️ THERE IS NOTHING TO ASSIGN. Membership is derived from status, so joining
    a drip means holding a status it accepts - this sets the drip's first accepted
    status on the lead. It used to write drip_campaign_id, which is the column the
    redesign removed precisely because it could disagree with the status.
    """
    steps = drip.steps(cid)
    accepted = (campaigns.get(cid) or {}).get('accepted_statuses') or ['emailed']
    with db.cursor() as cur:
        cur.execute('UPDATE leads SET status = %s WHERE lead_id = %s',
                    (accepted[0], lid))
        for i, st in enumerate(steps[:sent_steps], start=1):
            # WITH A TOKEN. A send row without one cannot be clicked, and a
            # click test whose click silently does not happen passes for the
            # wrong reason - which is exactly how break 100 caught this.
            # ⚠️ sent_by IS REQUIRED whenever sent_at is set
            # (email_sends_sent_is_attributed). This fixture used to omit it,
            # fabricating the exact shape migration 042 exists to forbid: a row
            # claiming a send that nobody performed. A fixture that builds
            # impossible data tests nothing about the real thing.
            cur.execute("""INSERT INTO email_sends
                               (lead_id, step_id, seq, to_email, sent_at,
                                sent_by, click_token)
                           VALUES (%s,%s,%s,'pat@whitfield.test', now(),
                                   'operator', %s)""",
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
    """
    The gate. Once replied_at is set, nothing more goes out.

    ⚠️ THE GATE MUST ACCEPT `engaged`, OR THIS TEST PROVES NOTHING.
    record_reply() sets replied_at AND status = 'engaged'. With a gate of
    ['emailed'] the STATUS excludes the lead, REPLIED_STOP is never consulted,
    and removing it changes nothing - break 99 reported GREEN on the full pass
    for exactly that reason.

    ⚠️ AND THIS IS THE GUARD THAT MATTERS MOST. Reply detection is manual, so
    REPLIED_STOP is what stands between a firm that answered and another
    automated email. The status gate happens to cover it TODAY only because no
    drip accepts `engaged` - and accepting `engaged` is a configuration the
    operator is explicitly allowed to choose. A guard that is covered by
    accident is not covered.

    So the gate accepts `engaged` here, and only REPLIED_STOP can do the work.
    """
    from api import campaigns as _c
    cid = dripc['campaign_id']
    _c.update(cid, accepted_statuses=['emailed', 'engaged'])
    lid = _lead(db, days_ago=40)
    _join(db, lid, cid, sent_steps=1)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], 'premise'

    stages.record_reply(lid, note='not interested')
    assert _get_lead(db, lid)['status'] == 'engaged', \
        'the premise: a reply sets engaged, which this gate ACCEPTS'
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'a lead that replied is still being dripped - and the gate cannot be '\
        'what stopped it, because this gate accepts its status'


def test_a_click_does_NOT_stop_the_sequence(db, dripc):
    """
    A click is interest, not an answer. Stopping on one would silence the
    sequence exactly when it is working. ONLY A REPLY STOPS IT.

    ⚠️ AND A CLICK HAS ITS OWN STATUS SO THIS STAYS TRUE. clicks.record() used to
    advance to `engaged`, conflating "they replied" with "they read the sample" -
    harmless while status governed dialling, decisive once it governed SENDING,
    because a firm that read the sample stopped hearing from us.

    It advances to `clicked` now, one rung below `engaged`, and a drip accepting
    `clicked` keeps the sequence running. replied_at still stops everything,
    separately: REPLIED_STOP is not a status check.

    (This docstring described the OLD behaviour for one commit after the code
    changed, because an earlier edit to it failed silently and was not checked. A
    comment describing behaviour that has changed is a false witness - the same
    shape as masked-guard row 17.)
    """
    lid = _lead(db, days_ago=40)
    campaigns.update(dripc['campaign_id'],
                     accepted_statuses=['emailed', 'clicked'])
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
    # ON THE PAGE, not in a redirect: the refusal now re-renders so the typed
    # copy survives it. Asserting on the location header was asserting on the
    # mechanism that threw the work away.
    assert 'REJECTED' in _msg(r), _msg(r)
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
    # ⚠️ THE STATUS *IS* THE STOP. There is no assignment to clear: 'bad_email' is
    # not in any drip's accepted_statuses, so the lead falls out of every one of
    # them by the same mechanism that put it in. One fact, not two.
    assert not drip.qualifies_for(dict(row)), \
        'a bounced lead still qualifies for a drip'
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
    # It does own a sender, and that panel is CAMPAIGN identity - beside
    # Identity at the top, not below a step.
    assert '<h2>Sender</h2>' in r.text
    # ⚠️ AND IT DOES NOT EXPLAIN OUR SCHEMA BACK AT THE OPERATOR. Three panels
    # justifying campaign_id, the daily cap and the funnel are HANDOFF material,
    # not UI - nobody needs the app arguing its design while they write an email.
    for lecture in ('What a drip does not have', 'chaining lets',
                    'campaign_id</code> never moves', 'funnel honest'):
        assert lecture not in r.text, f'the editor still lectures: {lecture!r}'
    assert 'Every delay is measured from the <b>first send</b>' in r.text, \
        'the one line that replaced those paragraphs is missing'


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
            # QUALIFY IT, and stamp the clock step 1 runs from. status_changed_at
            # replaced drip_entered_at: with membership derived there is no entry
            # event, and the moment the lead began to qualify is the moment its
            # status changed.
            cur.execute("""UPDATE leads SET emailed_at = NULL, emailed_by = NULL,
                                  status = 'emailed', status_changed_at = now()
                            WHERE lead_id = %s""", (lid,))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'step 1 sent immediately despite a 30-minute delay'

    # Move the DATA, not the clock.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads
                              SET status_changed_at = now() - interval '31 minutes'
                            WHERE lead_id = %s""", (lid,))
    mine = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert [r['position'] for r in mine] == [1], 'step 1 never became due'


def test_qualifying_stamps_when_by_trigger(db, dripc):
    """
    ⚠️ STEP 1'S CLOCK. It used to run from drip_entered_at, stamped by enter().
    Membership is derived now, so there is no entry event - the moment the lead
    began to qualify is the moment its STATUS changed, and a trigger stamps that
    because status is written from six places and a column depending on all of
    them remembering is a column that is wrong.
    """
    lid = _lead(db, days_ago=0, phone_e164='+15553339002', status='new')
    before = _get_lead(db, lid)['status_changed_at']
    assert before is not None, 'a new lead has no status clock at all'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='emailed' WHERE lead_id=%s",
                        (lid,))
    after = _get_lead(db, lid)['status_changed_at']
    assert after > before, 'the status changed and the clock did not move'

    # AND AN UNRELATED EDIT LEAVES IT ALONE, or every save would restart step 1.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET notes='x' WHERE lead_id=%s", (lid,))
    assert _get_lead(db, lid)['status_changed_at'] == after, \
        "a non-status edit moved step 1's clock"


def test_stopping_a_lead_sets_a_status_no_drip_accepts(db, dripc):
    """
    ⚠️ STOPPING IS A STATUS CHANGE NOW, because there is nothing else to clear.
    Each reason maps to the status that MEANS it, so the stop and the pipeline
    cannot disagree - and the lead leaves every drip by the same mechanism that
    put it in. The reason survives: "it said no" and "it never answered" are
    different facts and only the status records which.
    """
    lid = _lead(db, days_ago=0, phone_e164='+15553339003')
    _join(db, lid, dripc['campaign_id'])
    assert drip.qualifies_for(_get_lead(db, lid)), 'the premise: it qualifies'

    assert drip.stop(lid, 'by_hand', by='test') is True
    row = _get_lead(db, lid)
    assert row['status'] == drip.STOP_STATUS['by_hand']
    assert not drip.qualifies_for(dict(row)), \
        "a stopped lead still qualifies for a drip"


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
    assert drip._maybe_finish(None, lid, cid) is True, \
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
                    data={'subject_0': 'Following up, {{first_name}}',
                          'body_0': 'Hi {{first_name}} at {{company}}.',
                          'lead_id': str(lead)})
    assert r.status_code == 200
    # KEYED BY STEP INDEX, like /email/preview returns every variant at once.
    j = r.json()['steps']['0']
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
                    data={'subject_0': 'Hi {{frist_name}}', 'body_0': 'x',
                          'lead_id': str(lead)})
    j = r.json()['steps']['0']
    assert 'frist_name' in j['unknown']
    assert j['subject'] == 'Hi {{frist_name}}', \
        'an unknown placeholder must render as itself, not be blanked - blanking '\
        'hides the typo instead of showing it'


def test_the_preview_reports_a_body_count(db, dripc, client):
    lead = _lead(db, days_ago=1, phone_e164='+15553339012')
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                    data={'subject_0': 's', 'body_0': 'one two three',
                          'lead_id': str(lead)})
    j = r.json()['steps']['0']
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
    rows = re.search(r'name="body_0"[^>]*rows="(\d+)"', r.text)
    assert rows and int(rows.group(1)) >= 16, \
        'the body box is shorter than the copy editor\'s 16 rows'


def test_each_step_collapses_and_the_delay_sits_between_them(db, dripc, client):
    """A four-step sequence has to fit on screen, and the delay is a property of
    the GAP rather than of the email - reading "send in 8 days" between two cards
    is how the sequence's rhythm becomes visible."""
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert '<details' in r.text, 'steps do not collapse'
    assert 'class="stepgap"' in r.text, \
        'the delay is not its own element between the steps'
    assert 'days after the first send' in r.text


def test_step_1_is_not_offered_a_day_field(db, dripc, client):
    """It IS the first send. A day field on it read as a control and was not one."""
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    # DAYS AFTER JOINING THE DRIP. Step 1 IS the first send, so its label must
    # never say "after the first send" - that is after itself.
    assert 'name="day1_0"' in r.text, 'step 1 has no timing control'
    assert 'joining the drip' in r.text, \
        'step 1 does not say what its delay is measured from'
    assert 'name="delay_0"' not in r.text, \
        'step 1 is still offered a days-after-the-first-send field'
    # ⚠️ ASSERT ON THE TEXT, NOT ONLY THE CLASS. The previous version checked
    # that no class="stepgap" preceded step 1, which was true of the SERVER
    # HTML while the CLONE TEMPLATE still carried one - so on an empty drip the
    # first step added got "days after the first send" above it. A class check
    # cannot see markup the browser builds; the template's own text can.
    first = r.text.index('<b>Step 1</b>')
    assert 'class="stepgap"' not in r.text[:first], 'a days row renders above step 1'
    tpl = r.text[r.text.index('<template id="steptpl">'):]
    tpl = tpl[:tpl.index('</template>')]
    assert 'after the first send' not in tpl, \
        'the clone template carries a gap row, so the FIRST step added by hand '\
        'is labelled "days after the first send" - and step 1 IS the first send'


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


# ==========================================================================
# THE THREE FAULTS FOUND BY USING IT IN THE LIVE APP
#
# All three passed the previous tests, because those tests asserted the markup
# was PRESENT and not that it WORKED. Rendering is not the same as functioning.
# ==========================================================================

def test_the_sequence_form_is_not_nested_inside_the_config_form(db, dripc, client):
    """
    ⚠️ THE BUG THAT MADE THE EDITOR UNUSABLE.

    HTML FORMS CANNOT NEST. The sequence <form> sat inside the campaign-config
    <form>, so the browser dropped the inner one and "Save the sequence"
    submitted the OUTER form to /campaign/{id}/save - which requires
    agent_l1_version, daily_cap, max_concurrent and the spacing fields. A drip
    campaign has none of them, so it 422'd with a list of missing call-only
    fields and no step could be added at all.

    Every earlier test passed because they all asserted the markup was PRESENT.
    Presence is not function, and this is the difference.
    """
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    html = r.text
    save_form = html.index('action="/campaign/%s/save"' % dripc['campaign_id'])
    seq_form = html.index('action="/campaign/%s/steps"' % dripc['campaign_id'])
    # The config form must be CLOSED before the sequence form opens.
    closed_at = html.index('</form>', save_form)
    assert closed_at < seq_form, (
        'the sequence form is nested inside the config form - the browser will '
        'drop it and the save will post to /save, which needs call-only fields')


def test_saving_a_sequence_needs_no_call_only_fields(db, dripc, client):
    """
    The functional half: post ONLY what the sequence form posts and it must
    work. No agent_l1_version, no daily_cap, no spacing.
    """
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps", data={
        'step_id_0': '', 'day1_0': '1', 'enabled_0': '1',
        'subject_0': 'Opener', 'body_0': 'Hi {{first_name}}',
        'delay_1': '4', 'enabled_1': '1',
        'subject_1': 'Second', 'body_1': 'Following up',
    }, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    assert 'REJECTED' not in _msg(r), _msg(r)
    saved = drip.steps(dripc['campaign_id'])
    assert [s['subject'] for s in saved] == ['Opener', 'Second']
    assert saved[0]['delay_minutes'] == 1440, 'one day, stored as minutes'
    assert saved[1]['delay_days'] == 4


def test_the_preview_panel_is_beside_the_editor(db, dripc, client):
    """
    ⚠️ SECTION 1 OF THE SPEC, and it was the main thing asked for. A dropdown
    naming the preview lead is not a preview: there has to be a PANEL holding
    the rendered output, beside the box being typed in.

    Asserts the panel exists, that it is inside the same split as the editor, and
    that it arrives already rendered rather than blank until the first keystroke.
    """
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    html = r.text
    # ⚠️ THE COPY EDITOR'S CLASSES, not a second set. .copy-split / .pv /
    # .pv-subject / .pv-body already existed and already worked; inventing
    # .step-split alongside them meant two sets of preview styles that drift.
    assert 'copy-split' in html, 'no side-by-side container'
    assert 'id="pv-0"' in html, 'step 1 has no preview panel'
    for invented in ('step-split', 'step-preview', 'step-edit'):
        assert invented not in html, f'{invented} is a second pattern'
    # The panel sits in the same split as the editor for that step.
    split = html.index('class="copy-split"')
    body = html.index('name="body_0"', split)
    prev = html.index('id="pv-0"', split)
    assert body < prev, 'the preview is not beside the editor'
    assert 'Opener' in html or 'Following up' in html or 'One' in html


def test_the_preview_panel_arrives_already_rendered(db, dripc, client):
    """Blank until the first keystroke would mean the preview is wrong exactly
    when the copy is being read for the first time."""
    cid = dripc['campaign_id']
    lead = _lead(db, days_ago=1, phone_e164='+15553339020',
                 company='Whitfield Law', dm_name='Timothy Ross')
    steps = drip.steps(cid)
    drip.save_steps(cid, [
        {'step_id': steps[0]['step_id'], 'delay_minutes': 0,
         'subject': 'Hi {{first_name}}', 'body': 'At {{company}}.',
         'enabled': '1'}])
    r = client.get(f'/campaign/{cid}')

    # ⚠️ ASSERT ON THE PANEL'S OWN CONTENTS, not on a global count. {{company}}
    # legitimately appears elsewhere on the page - in the textarea, which IS the
    # raw template, and in every placeholder menu - so a page-wide count says
    # nothing about the panel. Same fault as matching 'Prompt version' against a
    # CSS comment: assert the specific thing.
    start = r.text.index('id="pv-0"')
    panel = r.text[start:r.text.index('</div>', r.text.index('pv-warn', start))]
    assert 'Whitfield Law' in panel, \
        f'the panel is not rendered on load: {panel[:200]!r}'
    assert 'Timothy' in panel, 'the panel did not render the contact name'
    assert '{{company}}' not in panel and '{{first_name}}' not in panel, \
        'the panel is showing the raw template rather than rendered output'


def test_many_steps_can_be_added_without_a_reload(db, dripc, client):
    """
    "Add step 1" and nothing after meant one new step per save. There has to be
    a clone source and an append target, or the sequence length is capped by the
    number of blank rows the server happened to render.
    """
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert '<template id="steptpl">' in r.text, 'no clone source for new steps'
    assert 'id="newsteps"' in r.text, 'nowhere for a new step to be appended'
    assert 'id="addstep"' in r.text, 'no add-another-step control'
    assert '__I__' in r.text, 'the template has no index placeholder to substitute'


def test_the_sender_belongs_to_the_campaign_not_to_a_step(db, dripc, client):
    """
    From-address, from-name and footer are CAMPAIGN identity. Rendering them
    below step 1 read as though they were part of that step.

    They belong in the config form beside Identity, and - crucially - INSIDE it,
    because that is the form that saves them.
    """
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    html = r.text
    ident = html.index('<h2>Identity</h2>')
    sender = html.index('<h2>Sender</h2>')
    seq = html.index('The sequence')
    assert ident < sender < seq, \
        'the sender block is not with Identity above the sequence'
    # And it is still inside the CONFIG form, or saving it would do nothing.
    save_form = html.index('action="/campaign/%s/save"' % dripc['campaign_id'])
    closed_at = html.index('</form>', save_form)
    assert save_form < sender < closed_at, \
        'the sender fields fell outside the form that saves them'


# ==========================================================================
# A CALL-SOURCED LEAD MUST NEVER RECEIVE STEP 1
# ==========================================================================

def test_a_call_sourced_lead_never_receives_step_1(db, dripc):
    """
    ⚠️ THE BUG: a lead that joined a drip BEFORE the sequence was written had no
    step 1 to be linked to. Add the steps later and the days branch matched step
    1 - emailed_at set, delay_days 0 - so a firm we CALLED received step 1's cold
    opener, which says nothing about the call.

    enter() no longer mis-reports the link, but the STRUCTURAL fix is that step 1
    is not on the days path at all: `s.position > 1`. Step 1 is reachable only
    by a lead with NO emailed_at.
    """
    cid = dripc['campaign_id']
    # A stepless drip is a state the UI allows - /campaigns warns "no steps yet".
    for st in drip.steps(cid):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM drip_steps WHERE step_id = %s',
                            (st['step_id'],))
    assert drip.steps(cid) == []

    lid = _lead(db, days_ago=40, phone_e164='+15553339500')
    _join(db, lid, cid)

    # NOW write the sequence, as anyone would.
    drip.save_steps(cid, [
        {'delay_minutes': 0, 'subject': 'Cold opener', 'body': 'I found you on a list'},
        {'delay_days': 4, 'subject': 'Second', 'body': 'b'},
    ])

    due = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert 1 not in [r['position'] for r in due], (
        'a firm we CALLED is queued for step 1 - the cold opener that says '
        'nothing about the call')
    assert [r['position'] for r in due] == [2], \
        'it should pick up at step 2, at its delay from emailed_at'


def test_a_stepless_drip_selects_nobody_and_links_nothing(db, dripc):
    """
    ⚠️ REWRITTEN, NOT DELETED. This asserted that enter() wrote "⚠️ NO STEPS YET"
    to the timeline instead of the false "email 1 counts as step 1" - a real bug,
    where rowcount answered "did the UPDATE match a row" rather than "did it find
    a step".

    enter() is gone, so there is no entry moment to narrate. THE PROPERTY IT
    PROTECTED STILL MATTERS and is asserted directly: a drip with no steps selects
    nobody and links nothing, however many leads qualify. The "no steps yet"
    warning now lives on the campaign screen, where it is visible before anyone is
    affected rather than narrated after.
    """
    cid = dripc['campaign_id']
    for st in drip.steps(cid):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM drip_steps WHERE step_id = %s',
                            (st['step_id'],))
    assert drip.steps(cid) == []

    lid = _lead(db, days_ago=40, phone_e164='+15553339501')
    _join(db, lid, cid)
    assert drip.qualifies_for(_get_lead(db, lid)), 'the premise: it qualifies'

    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)] == [], \
        'a drip with no steps selected a lead'
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM email_sends
                            WHERE lead_id = %s AND step_id IS NOT NULL""",
                        (lid,))
            assert cur.fetchone()['n'] == 0, \
                'a step-less drip linked email 1 to a step that does not exist'

def test_the_editor_says_who_step_1_governs(db, dripc, client):
    """
    DERIVED, NOT DECLARED. A drip could carry a source column; that was rejected
    because step 1 is load-bearing for a call-sourced lead - hiding it would make
    the +4-day step position 1, and enter() would link email 1 to THAT, eating a
    step rather than skipping one.

    A count also stays true when both sources are mixed, which nothing prevents.
    """
    cid = dripc['campaign_id']
    r = client.get(f'/campaign/{cid}')
    assert 'No leads on this drip yet' in r.text

    # All call-sourced: step 1 governs none of them, and it says so.
    for n in range(2):
        lid = _lead(db, days_ago=40, phone_e164=f'+1555333960{n}')
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE leads SET status='emailed' "
                            'WHERE lead_id=%s', (lid,))
    # ⚠️ NORMALISE WHITESPACE. Jinja wraps the rendered text, so asserting a
    # literal one-line substring fails on markup that is perfectly correct -
    # brittle in the same way as matching a CSS comment was.
    def flat(t):
        return ' '.join(t.split())
    r = client.get(f'/campaign/{cid}')
    assert 'Step 1 governs <b>0</b> of the 2 leads' in flat(r.text), \
        'the label does not say step 1 governs none of them'
    assert 'All 2 arrived from calls' in flat(r.text)

    # Mixed: the number is what matters, in BOTH directions.
    lid = _lead(db, days_ago=0, phone_e164='+15553339610',
                lead_source='import')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='emailed' WHERE lead_id=%s",
                        (lid,))
    r = client.get(f'/campaign/{cid}')
    assert 'Step 1 governs <b>1</b> of the 3 leads' in flat(r.text), \
        'the label does not report the imported count when sources are mixed'
    assert '2 arrived from calls and already had' in flat(r.text)

    # AND IT STAYS EDITABLE AND PRESENT even when it governs nobody.
    assert 'name="subject_0"' in r.text and 'name="body_0"' in r.text, \
        'step 1 must stay editable - imported leads get added later'
    assert 'name="day1_0"' in r.text, 'step 1 lost its timing control'


# ==========================================================================
# THE PREVIEW MUST WORK BEFORE ANYONE IS ON THE DRIP
#
# Which is exactly when the copy is being written. It used to require
# dm_email IS NOT NULL, and on a real database that meant 1,085 of 1,087 leads
# were ineligible - a call list arrives as company + phone - so the picker
# offered one option: the operator's own test lead.
# ==========================================================================

def test_a_company_name_is_enough_to_preview_against(db, dripc):
    """
    An address is not needed to judge COPY. {{company}} and {{first_name}} are
    what make it read naturally, and a real firm name does that in a way
    "Example Firm LLC" cannot.
    """
    from api import drafts
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone)
                           VALUES ('Bergman & Co','+15553337001',
                                   'America/Los_Angeles')""")
    got = drafts.preview_candidates(dripc['campaign_id'])
    assert any(c['company'] == 'Bergman & Co' for c in got), \
        'a lead with a company and no email cannot be previewed against'


def test_the_example_is_a_pickable_option(db, dripc, client):
    """Not a silent last resort. It is in the dropdown, marked as an example, so
    nobody has to wonder whose data they are reading."""
    from api import drafts
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert f'value="{drafts.SAMPLE_ID}"' in r.text, 'the example is not offered'
    assert 'not a real lead' in r.text, 'the example is not marked as one'
    assert drafts.SAMPLE_LEAD['company'] in r.text


def test_the_preview_works_with_no_leads_at_all(db, dripc, client):
    """
    ⚠️ THE CASE THAT WAS BROKEN. Writing the copy happens BEFORE anyone is on the
    drip, so a preview that needs a lead is unavailable exactly when it is needed.
    """
    from api import drafts
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM email_clicks')
            cur.execute('DELETE FROM email_sends')
            cur.execute('DELETE FROM leads')
    assert drafts.preview_candidates(dripc['campaign_id']) == []

    # The page still offers a subject, and it is the example, pre-selected.
    r = client.get(f"/campaign/{dripc['campaign_id']}")
    assert r.status_code == 200
    sel = r.text[r.text.index('id="pv-lead"'):]
    sel = sel[:sel.index('</select>')]
    assert f'value="{drafts.SAMPLE_ID}"' in sel and 'selected' in sel, \
        'with no leads the example must be the pre-selected option'

    # And the endpoint renders against it.
    pr = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                     data={'subject_0': 'Hi {{first_name}}',
                           'body_0': 'At {{company}}.',
                           'lead_id': drafts.SAMPLE_ID})
    j = pr.json()
    assert j['lead']['real'] is False, 'the example must not report as a real lead'
    assert j['steps']['0']['subject'] == 'Hi %s' % \
        drafts.first_name(drafts.SAMPLE_LEAD['dm_name'])
    assert drafts.SAMPLE_LEAD['company'] in j['steps']['0']['body']


def test_an_unknown_lead_id_falls_back_to_the_example_not_a_stranger(db, dripc, client):
    """
    A stale id in the dropdown must not silently render against whichever lead
    happens to come first - that is somebody else's data appearing in a preview
    with nothing saying so.
    """
    import uuid
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                    data={'subject_0': 's', 'body_0': 'At {{company}}.',
                          'lead_id': str(uuid.uuid4())})
    j = r.json()
    assert j['lead']['real'] is False
    from api import drafts
    assert drafts.SAMPLE_LEAD['company'] in j['steps']['0']['body']


def test_the_endpoint_says_whether_the_subject_was_real(db, dripc, client):
    """The page prints this. An example that looks like a real firm is worse than
    no preview if nobody can tell which it is."""
    lead = _lead(db, days_ago=1, phone_e164='+15553337002',
                 company='Whitfield Law', dm_name='Timothy Ross')
    r = client.post(f"/campaign/{dripc['campaign_id']}/steps/preview",
                    data={'subject_0': 's', 'body_0': 'b', 'lead_id': str(lead)})
    assert r.json()['lead'] == {'company': 'Whitfield Law',
                               'name': 'Timothy Ross', 'real': True}


# ==========================================================================
# ADDING STEPS MUST NOT DESTROY THE ONES ALREADY WRITTEN
#
# Reported as: write step 1, add step 2, save -> ONE step containing step 2's
# content. That is copy somebody typed, destroyed, with a green banner.
# ==========================================================================

def _post_steps(client, cid, *steps, **extra):
    """Post exactly what the sequence form posts, with explicit indices."""
    data = dict(extra)
    for i, st in enumerate(steps):
        if st is None:
            continue
        data[f'subject_{i}'] = st['subject']
        data[f'body_{i}'] = st['body']
        data[f'enabled_{i}'] = '1'
        if i == 0:
            data[f'day1_{i}'] = str(st.get('day1', 0))
        else:
            data[f'delay_{i}'] = str(st['delay'])
    return client.post(f'/campaign/{cid}/steps', data=data,
                       follow_redirects=False)


def _msg(r):
    """
    What the operator actually sees, from a redirect OR a re-rendered refusal.

    A refused save no longer redirects - it re-renders the page with the typed
    copy still in it - so asserting on r.headers['location'] alone would KeyError
    on exactly the paths these tests exist to check.
    """
    import urllib.parse
    if r.status_code in (302, 303):
        return urllib.parse.unquote(r.headers['location'])
    return r.text


def _clear(cid):
    for st in drip.steps(cid):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM drip_steps WHERE step_id=%s',
                            (st['step_id'],))


def test_a_blank_delay_refuses_the_save_and_names_the_step(db, dripc, client):
    """
    ⚠️ A MISSING DELAY IS THE SAME CLASS AS A MISSING SUBJECT: refused, named,
    and nothing written. It WAS refused - with "step 3: '' is not a number of
    days", which is true and useless - and the refusal was invisible for a
    different reason (see the copy-back test below), so it read as a silent drop.
    """
    cid = dripc['campaign_id']
    _clear(cid)
    _post_steps(client, cid,
                {'subject': 'ONE', 'body': 'one', 'day1': 0},
                {'subject': 'TWO', 'body': 'two', 'delay': 3})
    before = [(s['position'], s['subject'], s['body'], s['delay_days'])
              for s in drip.steps(cid)]
    assert len(before) == 2

    ids = {f'step_id_{i}': str(s['step_id'])
           for i, s in enumerate(drip.steps(cid))}
    r = client.post(f'/campaign/{cid}/steps', data={
        'subject_0': 'ONE', 'body_0': 'one', 'enabled_0': '1', 'day1_0': '0',
        'subject_1': 'TWO', 'body_1': 'two', 'enabled_1': '1', 'delay_1': '3',
        # step 3 has copy and NO DELAY - what a fresh clone posts untouched.
        'subject_2': 'THREE subj', 'body_2': 'THREE body', 'enabled_2': '1',
        'delay_2': '',
        **ids}, follow_redirects=False)

    m = _msg(r)
    assert 'REJECTED' in m, m
    assert 'step 3' in m, f'the refusal does not say WHICH step: {m}'
    assert 'no delay' in m, f'the refusal does not say what is missing: {m}'
    # ⚠️ AND NOTHING CHANGED. A refusal that half-wrote would be worse than the
    # bug: the schedule would come from steps nobody approved.
    after = [(s['position'], s['subject'], s['body'], s['delay_days'])
             for s in drip.steps(cid)]
    assert after == before, f'a refused save changed rows: {before} -> {after}'


def test_step_1_cannot_be_given_a_delay_the_database_would_refuse(db, dripc, client):
    """
    ⚠️ THE CEILING IS THE DATABASE'S, AND THE SCREEN MUST NOT OFFER PAST IT.

    drip_steps_delay_minutes_sane caps delay_minutes at 10080 (seven days). The
    step 1 input offered 0-60 DAYS, so 30 was refused with "43200 minutes is over
    a week" - a number nobody typed in a unit nobody chose. Fixing only the
    message and raising the python ceiling to 60 turned that refusal into a 500
    from Postgres, which is why this test asserts BOTH: a refusal, and never an
    exception.
    """
    cid = dripc['campaign_id']
    _clear(cid)
    r = client.post(f'/campaign/{cid}/steps', data={
        'subject_0': 'ONE', 'body_0': 'one', 'enabled_0': '1',
        'day1_0': str(drip.MAX_STEP1_DAYS + 1),
    }, follow_redirects=False)
    assert r.status_code in (200, 303), \
        f'the ceiling reached the database instead of the operator ({r.status_code})'
    m = _msg(r)
    assert 'REJECTED' in m, m
    assert 'days' in m and 'minutes' not in m, \
        f'the refusal must speak in the unit that was typed: {m}'
    assert drip.steps(cid) == [], 'a refused step 1 was written anyway'
    # AND THE SCREEN AGREES WITH IT, so the value is never offered in the first
    # place. A client max is a convenience; the refusal above is the guard.
    page = client.get(f'/campaign/{cid}').text
    assert f'name="day1___I__" min="0" max="{drip.MAX_STEP1_DAYS}"' in page, \
        'the step 1 input offers days the save refuses'


def test_a_refused_save_gives_back_the_copy_that_was_typed(db, dripc, client):
    """
    ⚠️ THE ACTUAL LOSS. The refusal was correct; the 303 that carried it
    re-rendered the sequence FROM THE DATABASE, so the step being complained
    about vanished from the screen along with its copy. The operator saw work
    disappear and no error, because the banner sits ~90 lines above the #drip
    anchor the redirect jumps to - a message the browser scrolls past.
    """
    cid = dripc['campaign_id']
    _clear(cid)
    r = client.post(f'/campaign/{cid}/steps', data={
        'subject_0': 'FIRST subj', 'body_0': 'FIRST body',
        'enabled_0': '1', 'day1_0': '0',
        'subject_1': 'UNSAVED SUBJECT', 'body_1': 'UNSAVED BODY WORTH KEEPING',
        'enabled_1': '1', 'delay_1': '',
    }, follow_redirects=False)

    assert r.status_code == 200, \
        f'a refusal must re-render, not redirect the copy away (got {r.status_code})'
    body = r.text
    assert 'UNSAVED SUBJECT' in body, 'the refused subject was thrown away'
    assert 'UNSAVED BODY WORTH KEEPING' in body, 'the refused body was thrown away'
    assert 'FIRST subj' in body, 'the step BEFORE the bad one was thrown away too'
    # AND THE REASON IS WHERE THE SEQUENCE IS, below the anchor the save jumps to.
    assert 'REJECTED' in body
    assert body.index('id="drip"') < body.rindex('REJECTED'), \
        'the refusal renders only above #drip, which is where it was unreadable'


def test_a_blank_row_is_skipped_not_refused(db, dripc, client):
    """
    ⚠️ "leave blank to skip" IS WHAT THE TEMPLATE PROMISES, and it was a lie: an
    untouched clone refused the whole save with "step 3 has no subject", so
    adding a step you then thought better of blocked saving the two you meant.

    It also MASKED THE COUNT GUARD, which is the serious half. `kept` counted
    steps WITH copy; `rows` counted every posted row. A blank row could stand in
    the place of a real step that had been dropped - the two numbers matched and
    the guard stayed silent over exactly the loss it exists to catch.
    """
    cid = dripc['campaign_id']
    _clear(cid)
    r = client.post(f'/campaign/{cid}/steps', data={
        'subject_0': 'ONE', 'body_0': 'one', 'enabled_0': '1', 'day1_0': '0',
        'subject_1': 'TWO', 'body_1': 'two', 'enabled_1': '1', 'delay_1': '4',
        # a clone nobody typed in: no subject, no body, no delay
        'subject_2': '', 'body_2': '', 'enabled_2': '1', 'delay_2': '',
    }, follow_redirects=False)

    m = _msg(r)
    assert 'REJECTED' not in m, f'an untouched blank row blocked the save: {m}'
    # NOT COUNTED, either: a blank row must not inflate the number the guard
    # compares against, or it can hide a dropped step behind a matching total.
    assert 'WROTE' not in m, f'the blank row was counted as a posted step: {m}'
    got = drip.steps(cid)
    assert [s['subject'] for s in got] == ['ONE', 'TWO'], \
        f'expected the two typed steps only, got {[s["subject"] for s in got]}'


def test_three_added_steps_save_as_three_rows_with_the_right_content(db, dripc, client):
    """
    ⚠️ THE CONTENT, NOT JUST THE COUNT. "three rows exist" would pass while every
    row held the same copy, which is the shape of the bug: one row containing the
    LAST step's content.
    """
    cid = dripc['campaign_id']
    for st in drip.steps(cid):                      # start from empty
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM drip_steps WHERE step_id=%s',
                            (st['step_id'],))
    r = _post_steps(client, cid,
                    {'subject': 'ONE subj', 'body': 'ONE body', 'day1': 0},
                    {'subject': 'TWO subj', 'body': 'TWO body', 'delay': 4},
                    {'subject': 'THREE subj', 'body': 'THREE body', 'delay': 10})
    assert r.status_code == 303, _msg(r)
    assert 'REJECTED' not in _msg(r), _msg(r)

    got = drip.steps(cid)
    assert len(got) == 3, f'{len(got)} rows, expected 3'
    assert [s['position'] for s in got] == [1, 2, 3]
    # EACH row's own content - the assertion that catches an overwrite.
    assert [s['subject'] for s in got] == ['ONE subj', 'TWO subj', 'THREE subj']
    assert [s['body'] for s in got] == ['ONE body', 'TWO body', 'THREE body']
    assert got[0]['delay_minutes'] == 0 and got[0]['delay_days'] == 0
    assert [s['delay_days'] for s in got[1:]] == [4, 10]


def test_a_step_added_to_an_existing_one_does_not_overwrite_it(db, dripc, client):
    """The exact reported repro: one step saved, then a second added."""
    cid = dripc['campaign_id']
    for st in drip.steps(cid):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM drip_steps WHERE step_id=%s',
                            (st['step_id'],))
    _post_steps(client, cid, {'subject': 'KEEP ME', 'body': 'MY COPY', 'day1': 0})
    first = drip.steps(cid)
    assert len(first) == 1 and first[0]['subject'] == 'KEEP ME'

    # Now the page reloads with step 1 at index 0 and a clone at index 1.
    r = _post_steps(client, cid,
                    {'subject': 'KEEP ME', 'body': 'MY COPY', 'day1': 0},
                    {'subject': 'ADDED', 'body': 'NEW COPY', 'delay': 4},
                    **{'step_id_0': str(first[0]['step_id'])})
    assert 'REJECTED' not in _msg(r), _msg(r)
    got = drip.steps(cid)
    assert len(got) == 2, f'{len(got)} rows - the first step was destroyed'
    assert got[0]['subject'] == 'KEEP ME' and got[0]['body'] == 'MY COPY', \
        "step 1's copy was overwritten by the step that was added after it"
    assert got[1]['subject'] == 'ADDED'


def test_non_contiguous_indices_are_all_saved(db, dripc, client):
    """
    ⚠️ THE SILENT DROP. The handler used to walk i = 0, 1, 2 ... and STOP AT THE
    FIRST GAP, so a step at index 3 with nothing at index 2 was dropped with the
    save reporting success. Nothing guarantees the DOM's indices are contiguous -
    a removed step, a failed clone, a re-fired handler, any of it leaves a hole -
    and a loop that stops at a hole treats "I could not see it" as "not there".
    """
    cid = dripc['campaign_id']
    for st in drip.steps(cid):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM drip_steps WHERE step_id=%s',
                            (st['step_id'],))
    r = client.post(f'/campaign/{cid}/steps', data={
        'subject_0': 'ONE', 'body_0': 'one', 'enabled_0': '1', 'day1_0': '0',
        # index 1 deliberately ABSENT - the hole
        'subject_2': 'THREE', 'body_2': 'three', 'enabled_2': '1', 'delay_2': '4',
        'subject_5': 'SIX', 'body_5': 'six', 'enabled_5': '1', 'delay_5': '10',
    }, follow_redirects=False)
    assert 'REJECTED' not in _msg(r), _msg(r)
    got = drip.steps(cid)
    assert [s['subject'] for s in got] == ['ONE', 'THREE', 'SIX'], \
        f'a gap in the indices dropped a step: {[s["subject"] for s in got]}'


def test_the_save_refuses_when_fewer_rows_would_be_written(db, dripc, client, monkeypatch):
    """
    THE GUARD. Whatever goes wrong between the form and the database, writing
    FEWER steps than were on the screen must never look like success. It does not
    care WHY the numbers differ - and it says NOTHING WAS WRITTEN only because it
    runs BEFORE the write.
    """
    cid = dripc['campaign_id']
    # Simulate the old contiguous walk by making one index invisible to the
    # collector, which is what a dropped step looked like.
    import api.web as w
    real = w._drip_mod.save_steps
    monkeypatch.setattr(w._drip_mod, 'save_steps',
                        lambda c, rows: real(c, rows[:1]))
    before = [s['subject'] for s in drip.steps(cid)]
    r = _post_steps(client, cid,
                    {'subject': 'A', 'body': 'a', 'day1': 0},
                    {'subject': 'B', 'body': 'b', 'delay': 4})
    loc = _msg(r)
    assert 'WROTE' in loc or 'REJECTED' in loc, loc
    assert 'incomplete' in loc or 'NOTHING was written' in loc, loc


# ==========================================================================
# a send row is a record of something that happened
# ==========================================================================

def test_the_database_refuses_a_send_row_with_no_recipient(db, dripc):
    """
    ⚠️ NOT NULL DID NOT STOP THIS. `to_email text NOT NULL` was already there, and
    migration 036's backfill wrote coalesce(dm_email, '') to satisfy it - so two
    rows existed claiming a send to nobody, one of them linked to a drip step, and
    the roster counted it as a step delivered.

    A writer that must satisfy NOT NULL and has nothing to write will write ''.
    The CHECK removes the option, so no future backfill can make the same trade.
    """
    import psycopg2
    lid = _lead(db, days_ago=1, phone_e164='+15553331001')
    with pytest.raises(psycopg2.errors.CheckViolation):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO email_sends
                                   (lead_id, seq, to_email, click_token)
                               VALUES (%s, 9, '', 'tok-empty-to')""", (lid,))


def test_the_database_refuses_a_sent_row_with_no_sender(db, dripc):
    """
    SENT IS AN ACT AND AN ACT HAS AN ACTOR. sent_at was `NOT NULL DEFAULT now()`
    in 036, so any insert that did not mention it declared a send - the DEFAULT
    did the lying. Requiring sent_by alongside makes it something that was done.
    """
    import psycopg2
    lid = _lead(db, days_ago=1, phone_e164='+15553331002')
    with pytest.raises(psycopg2.errors.CheckViolation):
        with dbm.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO email_sends
                                   (lead_id, seq, to_email, sent_at, click_token)
                               VALUES (%s, 9, 'a@b.test', now(), 'tok-no-by')""",
                            (lid,))


def test_record_send_refuses_a_lead_with_no_address_by_name(db, dripc):
    """
    The same rule said in the caller's terms, so the message names the LEAD
    rather than surfacing a constraint name from three layers down. Both exist on
    purpose: the CHECK is what makes it impossible, this is what makes it legible.
    """
    lid = _lead(db, days_ago=1, phone_e164='+15553331003', dm_email=None)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            with pytest.raises(ValueError, match='no email address'):
                drip.record_send(cur, lid, None, 1, '', None, 'operator')


def test_a_prepared_row_may_have_no_sent_at_but_still_needs_an_address(db, dripc):
    """
    PREPARED IS A REAL STATE - a draft whose token is live before the mail goes -
    and it must stay possible. What it may not be is anonymous.
    """
    lid = _lead(db, days_ago=1, phone_e164='+15553331004')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO email_sends
                               (lead_id, seq, to_email, click_token)
                           VALUES (%s, 9, 'real@firm.test', 'tok-prepared-ok')
                        RETURNING send_id, sent_at, sent_by""", (lid,))
            row = cur.fetchone()
    assert row['sent_at'] is None and row['sent_by'] is None, \
        'a prepared row must not claim to have been sent'


def test_no_token_is_minted_for_a_lead_with_no_address(db):
    """
    A tracked link for a lead we cannot email is a link nobody will ever click,
    and minting one meant writing the empty-recipient row this all started with.
    """
    from api import clicks as _clicks
    lid = _lead(db, days_ago=1, phone_e164='+15553331005', dm_email=None,
                has_confirmed_email=False, dm_email_confirmed=False)
    assert _clicks.token_for(lid) is None, \
        'a token was minted for a lead with nowhere to send it'


def test_a_click_sets_clicked_not_engaged(db, dripc):
    """
    ⚠️ `engaged` MEANS THEY REPLIED. A click is interest, not an answer - the
    standing rule since click tracking was built, and it decides whether a firm
    keeps hearing from us now that status governs sending.

    clicks.record() used to advance to `engaged`, conflating the two. A drip
    accepting `emailed` but not `clicked` still loses the lead when it clicks -
    that is the gate working as designed and the operator's call. What must never
    happen is a click being recorded as an ANSWER.
    """
    from api import campaigns as _c
    lid = _lead(db, days_ago=40, phone_e164='+15553339777')
    _c.update(dripc['campaign_id'], accepted_statuses=['emailed'])
    _join(db, lid, dripc['campaign_id'], sent_steps=1)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'the premise: it is due before the click'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT click_token FROM email_sends
                            WHERE lead_id = %s AND click_token IS NOT NULL
                            ORDER BY seq LIMIT 1""", (lid,))
            tok = cur.fetchone()['click_token']
    assert clicks.record(tok, user_agent='t') is not None
    assert _get_lead(db, lid)['status'] == 'clicked', \
        'a click was recorded as `engaged` - that means they ANSWERED'
    assert _get_lead(db, lid)['replied_at'] is None, \
        'a click set replied_at - only a reply may do that'
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'the gate did not drop a lead whose status left the accepted set'


# ==========================================================================
# THE STATUS GATE. Membership is derived, continuously, from one fact.
# ==========================================================================

def test_an_engaged_lead_is_not_selected_by_a_drip_accepting_emailed(db, dripc):
    """
    ⚠️ THE BUG THIS REDESIGN EXISTS FOR. Under the old model a lead was routed
    into a drip at email-1 time and stayed there whatever happened next, so
    `status` and `drip_campaign_id` were two facts saying one thing and could
    disagree: a lead marked `engaged` - it answered - was still queued for the
    next step, because nothing consulted the status.

    The gate makes the disagreement impossible rather than guarded against.
    """
    from api import campaigns as _c
    cid = dripc['campaign_id']
    _c.update(cid, accepted_statuses=['emailed'])
    lid = _lead(db, days_ago=11, phone_e164='+15553338001')
    _join(db, lid, cid, sent_steps=1)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'the premise: an emailed lead is due'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='engaged' WHERE lead_id=%s",
                        (lid,))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'an engaged lead is still queued for a step - the exact reported bug'


def test_a_status_change_stops_the_next_send_with_no_other_action(db, dripc):
    """
    RULE 1: CONTINUOUS, NOT ON ENTRY. Nothing has to notice and act - no stop
    flag, no sweep, no reconciliation. The gate is re-read on every selection.
    """
    from api import campaigns as _c
    cid = dripc['campaign_id']
    _c.update(cid, accepted_statuses=['emailed'])
    lid = _lead(db, days_ago=11, phone_e164='+15553338002')
    _join(db, lid, cid, sent_steps=1)
    was = [r['position'] for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert was, 'the premise'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='paused' WHERE lead_id=%s",
                        (lid,))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    # AND NOTHING ELSE WAS WRITTEN - no stop record, because nothing stopped it.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM activity
                            WHERE lead_id = %s AND summary LIKE 'drip STOPPED%%'""",
                        (lid,))
            assert cur.fetchone()['n'] == 0, \
                'something had to act - the gate should need no help'


def test_a_dnc_lead_qualifies_for_nothing_whatever_the_gate_says(db, dripc):
    """
    ⚠️ EVEN IF A GATE ACCEPTS IT. `dnc` is in TERMINAL_STOP, and suppression is
    keyed on the phone and outlives the lead. A gate is an operator's choice; the
    exclusion lists are not, and they must win.
    """
    from api import campaigns as _c
    cid = dripc['campaign_id']
    # deliberately hostile configuration
    _c.update(cid, accepted_statuses=['dnc', 'emailed'])
    lid = _lead(db, days_ago=11, phone_e164='+15553338003')
    _join(db, lid, cid, sent_steps=1)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='dnc' WHERE lead_id=%s", (lid,))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'a dnc lead was selected because a gate accepted the status'


def test_email_1_is_attributed_to_step_1_so_the_sequence_can_finish(db, dripc):
    """
    ⚠️ WHAT THE LAZY LINK ACTUALLY CARRIES - and it is NOT duplicate prevention.
    DUE_NOW selects position 1 only when `emailed_at IS NULL`, so a call-sourced
    lead can never be picked for step 1 whatever the link does. Break 131 reported
    GREEN and that is how I found out I had guarded the wrong thing.

    It carries two facts instead:

      step_stats     email 1's row has step_id NULL until it is linked, so step 1
                     reads "0 sent" for every call-sourced lead and its click rate
                     divides by a denominator that excludes everyone who got it
      _maybe_finish  counts done steps by joining email_sends to drip_steps, so an
                     unlinked step 1 means `done < total` FOREVER - the lead never
                     finishes and stays in the drip permanently
    """
    from api.config import load_config
    from api import campaigns as _c
    cid = dripc['campaign_id']
    _c.update(cid, accepted_statuses=['emailed'])
    lid = _lead(db, days_ago=40, phone_e164='+15553338100')
    _join(db, lid, cid)          # emailed_at is set: email 1 already went

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO email_sends
                               (lead_id, seq, to_email, sent_at, sent_by,
                                from_email, click_token)
                           VALUES (%s, 1, 'pat@whitfield.test', now(),
                                   'operator', 'info@counselorai.io',
                                   'tok-attrib-1')""", (lid,))
    due = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert due, 'the premise: a later step is due'
    drip.send_step(load_config(), due[0])

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT step_id FROM email_sends
                            WHERE lead_id = %s AND seq = 1""", (lid,))
            linked = cur.fetchone()['step_id']
    assert linked is not None, \
        "email 1 was never attributed to step 1 - step 1 will read '0 sent' and " \
        'the sequence can never count as finished'
    assert linked == drip.steps(cid)[0]['step_id'], 'linked to the wrong step'
    stats = {r['position']: r for r in drip.step_stats(cid)}
    assert stats[1]['sent'] == 1, \
        f"step 1 still reads {stats[1]['sent']} sent for a lead that received it"


def test_a_reply_still_outranks_a_click_and_stops_everything(db, dripc):
    """
    replied_at is unchanged and separate: REPLIED_STOP is not a status check, so a
    reply stops the sequence whatever any gate accepts. The ladder is forward-only
    - a click promotes emailed -> clicked, a reply promotes either to engaged.
    """
    from api import campaigns as _c, pipeline
    cid = dripc['campaign_id']
    _c.update(cid, accepted_statuses=['emailed', 'clicked', 'engaged'])
    lid = _lead(db, days_ago=11, phone_e164='+15553338200')
    _join(db, lid, cid, sent_steps=1)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], 'the premise'

    # a gate that accepts `engaged` still cannot outrank the reply itself
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads SET status='engaged', replied_at=now()
                            WHERE lead_id=%s""", (lid,))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'a replied lead was still selected because a gate accepted `engaged`'

    # AND THE RUNGS ARE IN ORDER: clicked cannot go back to emailed.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='clicked' WHERE lead_id=%s",
                        (lid,))
            assert pipeline.advance(cur, lid, 'emailed', 'test') is False, \
                'the pipeline went backwards from clicked to emailed'
            assert pipeline.advance(cur, lid, 'engaged', 'test') is True, \
                'a reply could not promote a clicked lead'
