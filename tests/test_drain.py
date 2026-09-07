"""
Drain: the three traps, and the disposition branch.

Each trap here is a test that fails loudly if the handling is removed. They
are the reason this phase does not cost an afternoon each.
"""

import json

import pytest

from api import drain


def _event(db, call_id, event, call):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO webhook_events (call_id, event, payload)
               VALUES (%s, %s, %s)""",
            (call_id, event, json.dumps({'event': event, 'call': call})),
        )
    db.commit()


def _lead(db, lead_id):
    with db.cursor() as cur:
        cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lead_id,))
        return cur.fetchone()


def _call_obj(lead_id, call_id='c1', **kw):
    base = {
        'call_id': call_id,
        'agent_id': 'agent_test',
        'metadata': {'lead_id': str(lead_id)},
        'start_timestamp': 1_700_000_000_000,
        'end_timestamp': 1_700_000_045_000,
        'disconnection_reason': 'user_hangup',
        'call_status': 'ended',
        'transcript': 'Agent: hi\nUser: sure',
    }
    base.update(kw)
    return base


def _analysis(**fields):
    return {'call_summary': 's', 'custom_analysis_data': fields}


# --------------------------------------------------------------------------
# TRAP 1 - call_analysis is on call_analyzed, never on call_ended
# --------------------------------------------------------------------------

def test_call_ended_writes_telephony_only_never_extraction(db, lead):
    """
    A call_ended payload has NO call_analysis. If the extraction fields were
    read here they would come back null and read as 'extraction is broken'.
    """
    _event(db, 'c1', 'call_ended', _call_obj(lead['lead_id']))
    assert drain.drain_once() == 1

    with db.cursor() as cur:
        cur.execute("SELECT * FROM calls WHERE call_id='c1'")
        call = cur.fetchone()
    assert call['duration_ms'] == 45_000
    assert call['disconnection_reason'] == 'user_hangup'
    assert call['analysis'] is None          # nothing extracted yet

    row = _lead(db, lead['lead_id'])
    assert row['dm_email'] is None
    assert row['dm_name'] is None


def test_latency_block_is_stored(db, lead):
    """Logged from day one - it is what tells us whether P50 is us or them."""
    latency = {'e2e': {'p50': 1200, 'p90': 1900}, 'llm': {'p50': 400}}
    _event(db, 'c1', 'call_ended', _call_obj(lead['lead_id'], latency=latency))
    drain.drain_once()
    with db.cursor() as cur:
        cur.execute("SELECT latency FROM calls WHERE call_id='c1'")
        assert cur.fetchone()['latency']['e2e']['p50'] == 1200


# --------------------------------------------------------------------------
# TRAP 2 - unconnected calls skip call_started but still fire the other two
# --------------------------------------------------------------------------

@pytest.mark.parametrize('reason', ['dial_failed', 'dial_no_answer', 'dial_busy'])
def test_unconnected_call_goes_dialing_to_no_answer(db, lead, reason):
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='dialing' WHERE lead_id=%s",
                    (lead['lead_id'],))
    db.commit()

    call = _call_obj(lead['lead_id'], disconnection_reason=reason,
                     transcript=None)
    _event(db, 'c1', 'call_ended', call)
    _event(db, 'c1', 'call_analyzed', call)      # fires even with no analysis
    assert drain.drain_once() == 2

    row = _lead(db, lead['lead_id'])
    assert row['status'] == 'no_answer'
    assert row['attempts'] == 1
    # PHASE 2: backoff is now per REASON, not a flat ladder.
    #   dial_busy      -> 15 minutes (someone IS there, come back soon)
    #   dial_no_answer -> 2 hours
    #   dial_failed    -> 2 hours
    # Whatever the rung, it must actually move, or the dialer re-dials an
    # unconnectable number on the very next tick.
    expected = {'dial_busy': 'busy', 'dial_no_answer': 'no_answer',
                'dial_failed': 'no_answer'}[reason]
    floor = "interval '10 minutes'" if expected == 'busy' else "interval '90 minutes'"
    with db.cursor() as cur:
        cur.execute(f"""SELECT next_attempt_at > now() + {floor} AS backed_off,
                              last_outcome
                         FROM leads WHERE lead_id = %s""", (lead['lead_id'],))
        row = cur.fetchone()
    assert row['backed_off'] is True
    assert row['last_outcome'] == expected


def test_backoff_ladder_then_max_attempts(db, lead):
    call = _call_obj(lead['lead_id'], disconnection_reason='dial_no_answer',
                     transcript=None)
    for i in range(1, 5):
        with db.cursor() as cur:
            cur.execute('DELETE FROM webhook_events')
        db.commit()
        _event(db, f'c{i}', 'call_analyzed',
               {**call, 'call_id': f'c{i}'})
        drain.drain_once()
    row = _lead(db, lead['lead_id'])
    assert row['attempts'] == 4
    assert row['status'] == 'max_attempts'


# --------------------------------------------------------------------------
# TRAP 3 - custom fields are ABSENT when no conversation happened
# --------------------------------------------------------------------------

def test_absent_custom_fields_never_write_the_string_undefined(db, lead):
    """The failure this prevents: dm_email = 'undefined'."""
    call = _call_obj(lead['lead_id'], call_analysis={'call_summary': 'x'})
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()

    row = _lead(db, lead['lead_id'])
    for col in ('dm_email', 'dm_name', 'dm_title'):
        assert row[col] is None, f'{col} = {row[col]!r}'


@pytest.mark.parametrize('junk', ['undefined', 'null', 'None', '', '   ', 'N/A'])
def test_junk_strings_are_treated_as_absent(db, lead, junk):
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        email_address=junk, decision_maker_name=junk))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()
    row = _lead(db, lead['lead_id'])
    assert row['dm_email'] is None
    assert row['dm_name'] is None


def test_absent_boolean_is_none_not_false(db):
    """Unknown must not silently become 'not confirmed'... or 'confirmed'."""
    assert drain._bool_field({'custom_analysis_data': {}}, 'x') is None
    assert drain._bool_field({'custom_analysis_data': {'x': True}}, 'x') is True
    assert drain._bool_field({'custom_analysis_data': {'x': 'false'}}, 'x') is False


# --------------------------------------------------------------------------
# disposition branch
# --------------------------------------------------------------------------

def test_confirmed_email_completes_the_lead(db, lead):
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='gave_info',
        decision_maker_name='Sara Whitfield',
        decision_maker_title='Intake Manager',
        email_address='sara@firm.com',
        email_spelled_back_confirmed=True))
    _event(db, 'c1', 'call_ended', call)
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()

    row = _lead(db, lead['lead_id'])
    assert row['status'] == 'completed'
    assert row['dm_name'] == 'Sara Whitfield'
    assert row['dm_email'] == 'sara@firm.com'
    assert row['dm_email_confirmed'] is True


def test_unconfirmed_email_goes_to_human_review(db, lead):
    """A wrong email is a dead lead that looks like a live one."""
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='gave_info',
        email_address='sara@firm.com',
        email_spelled_back_confirmed=False))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()

    row = _lead(db, lead['lead_id'])
    assert row['status'] == 'human_review'
    assert row['dm_email'] == 'sara@firm.com'
    assert row['dm_email_confirmed'] is False


def test_remove_me_writes_suppression_and_dnc_in_one_transaction(db, lead):
    call = _call_obj(lead['lead_id'],
                     call_analysis=_analysis(gatekeeper_disposition='remove_me'))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()

    row = _lead(db, lead['lead_id'])
    assert row['status'] == 'dnc'
    with db.cursor() as cur:
        cur.execute('SELECT * FROM suppression WHERE phone_e164 = %s',
                    (lead['phone_e164'],))
        s = cur.fetchone()
    assert s is not None and s['reason'] == 'requested'


def test_send_email_is_a_lane_not_a_failure(db, lead):
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='send_email', email_address='intake@firm.com'))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()

    row = _lead(db, lead['lead_id'])
    assert row['status'] == 'email_path'      # leaves the dialer entirely
    assert row['dm_email'] == 'intake@firm.com'
    assert row['attempts'] == 0                # NOT retried


def test_refused_is_not_retried(db, lead):
    call = _call_obj(lead['lead_id'],
                     call_analysis=_analysis(gatekeeper_disposition='refused'))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()
    assert _lead(db, lead['lead_id'])['status'] == 'completed'


def test_a_name_is_banked_even_when_the_call_otherwise_failed(db, lead):
    """'James is back Tuesday' gives you dm_name NOW. Half the objective."""
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='callback',
        decision_maker_name='James Ortiz',
        callback_person='James',
        callback_requested=True))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()

    row = _lead(db, lead['lead_id'])
    assert row['dm_name'] == 'James Ortiz'     # banked
    assert row['status'] == 'callback'
    assert row['callback_person'] == 'James'
    assert row['callback_count'] == 1


def test_callback_does_not_loop_immediately(db, lead):
    """next_attempt_at must move, or the dialer re-dials on the next tick."""
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='callback', callback_requested=True))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()
    with db.cursor() as cur:
        cur.execute("""SELECT next_attempt_at > now() + interval '1 hour' AS future
                         FROM leads WHERE lead_id = %s""", (lead['lead_id'],))
        assert cur.fetchone()['future'] is True


def test_fourth_callback_stops_asking(db, lead):
    with db.cursor() as cur:
        cur.execute('UPDATE leads SET callback_count=3 WHERE lead_id=%s',
                    (lead['lead_id'],))
    db.commit()
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='callback', callback_requested=True))
    _event(db, 'c1', 'call_analyzed', call)
    drain.drain_once()
    assert _lead(db, lead['lead_id'])['status'] == 'completed'


# --------------------------------------------------------------------------
# inbox mechanics
# --------------------------------------------------------------------------

def test_processed_events_are_not_reprocessed(db, lead):
    call = _call_obj(lead['lead_id'], call_analysis=_analysis(
        gatekeeper_disposition='gave_info', decision_maker_name='Sara',
        email_address='s@f.com', email_spelled_back_confirmed=True))
    _event(db, 'c1', 'call_analyzed', call)
    assert drain.drain_once() == 1
    assert drain.drain_once() == 0        # nothing left to do


def test_a_poison_event_records_the_error_and_stops_spinning(db):
    """No lead, malformed shape - must not loop forever."""
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO webhook_events (call_id, event, payload)
               VALUES ('bad', 'call_analyzed', %s)""",
            (json.dumps({'event': 'call_analyzed', 'call': None}),))
    db.commit()
    drain.drain_once()
    with db.cursor() as cur:
        cur.execute("SELECT processed_at, attempts FROM webhook_events WHERE call_id='bad'")
        row = cur.fetchone()
    assert row['processed_at'] is not None or row['attempts'] >= 1


# --------------------------------------------------------------------------
# regression: metadata carrying a lead_id that does not exist
# --------------------------------------------------------------------------

def test_unknown_metadata_lead_id_does_not_poison_the_inbox(db):
    """
    Found against the running container: metadata.lead_id was trusted without
    checking the table, so a stale or forged id produced a foreign key
    violation, failed five times, and parked the event forever.
    """
    call = _call_obj('90ed0655-c0da-485a-a878-17d4d46db7b6', call_id='c_orphan')
    _event(db, 'c_orphan', 'call_ended', call)
    assert drain.drain_once() == 1

    with db.cursor() as cur:
        cur.execute("""SELECT processed_at, attempts, last_error
                         FROM webhook_events WHERE call_id='c_orphan'""")
        row = cur.fetchone()
    assert row['processed_at'] is not None, 'event should be processed, not parked'
    assert row['attempts'] == 0
    assert row['last_error'] is None

    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls WHERE call_id='c_orphan'")
        assert cur.fetchone()['n'] == 0      # no orphan call row invented


def test_lead_is_still_found_by_last_call_id_when_metadata_is_absent(db, lead):
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET last_call_id='c_bylast' WHERE lead_id=%s",
                    (lead['lead_id'],))
    db.commit()
    call = _call_obj(lead['lead_id'], call_id='c_bylast')
    del call['metadata']
    _event(db, 'c_bylast', 'call_ended', call)
    drain.drain_once()
    with db.cursor() as cur:
        cur.execute("SELECT lead_id FROM calls WHERE call_id='c_bylast'")
        assert str(cur.fetchone()['lead_id']) == str(lead['lead_id'])
