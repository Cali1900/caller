"""
The L1 -> L2 -> L3 ladder.

The seam that matters most here is mark_emailed(): the "I emailed them" button
and the coming sequencer from demandcounselor.com are two CALLERS of one
transition. If that ever becomes two implementations, the automation is a
rewrite. A test asserts the web handler does not contain the transition.
"""

import datetime
import re

import pytest

from api import campaigns, dialer, drain, retell, stages

from conftest import running_campaign_id

LA = 'America/Los_Angeles'


def _lead(db, **kw):
    cols = {'company': 'Whitfield Law', 'phone_e164': '+15552220001',
            'timezone': LA, 'pool_status': 'active', 'status': 'new', 'has_confirmed_email': False,
            'campaign_id': running_campaign_id()}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


def _get(db, lid):
    with db.cursor() as cur:
        cur.execute('SELECT * FROM leads WHERE lead_id=%s', (lid,))
        return cur.fetchone()


@pytest.fixture
def enrolled(db, cfg_env, monkeypatch):
    """Standing queue: put leads in it and switch dialing on."""
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    from api import campaigns as c
    # The pause and the cap live on the campaign; settings has not been
    # consulted for either since the named-campaign change.
    c.update(running_campaign_id(), daily_cap=1000)

    def _queue(ids):
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        with db.cursor() as cur:
            cur.execute("UPDATE leads SET pool_status='active' "
                        "WHERE lead_id = ANY(%s::uuid[])", ([str(i) for i in ids],))
        db.commit()
    return _queue


# --------------------------------------------------------------------------
# L1 -> L2 on a CONFIRMED email only
# --------------------------------------------------------------------------

def test_a_confirmed_email_advances_l1_to_l2(db, cfg_env):
    lid = _lead(db, dm_email='sara@whitfieldlaw.com', dm_email_confirmed=True)
    with db.cursor() as cur:
        assert stages.advance_to_l2(cur, lid) is True
    db.commit()
    assert _get(db, lid)['has_confirmed_email'] is True


def test_an_unconfirmed_email_does_not_advance(db, cfg_env):
    """A wrong email is a dead lead that looks live. It must not walk the ladder."""
    lid = _lead(db, dm_email='typo@whitfieldlaw.com', dm_email_confirmed=False)
    with db.cursor() as cur:
        assert stages.advance_to_l2(cur, lid) is False
    db.commit()
    assert _get(db, lid)['has_confirmed_email'] is False


def test_no_email_does_not_advance(db, cfg_env):
    lid = _lead(db)
    with db.cursor() as cur:
        assert stages.advance_to_l2(cur, lid) is False
    db.commit()
    assert _get(db, lid)['has_confirmed_email'] is False


def test_the_drain_advances_the_ladder_on_a_confirmed_capture(db, cfg_env):
    """End to end through the real drain, not by calling stages directly."""
    import json
    lid = _lead(db)
    with db.cursor() as cur:
        cur.execute("""INSERT INTO calls (call_id, lead_id, stage) VALUES ('c1',%s,'L1')""", (lid,))
        payload = {'event': 'call_analyzed', 'call': {
            'call_id': 'c1', 'metadata': {'lead_id': str(lid)},
            'transcript': 'Agent: hi\nUser: sara@whitfieldlaw.com, yes correct',
            'disconnection_reason': 'user_hangup',
            'call_analysis': {'custom_analysis_data': {
                'gatekeeper_disposition': 'gave_info',
                'decision_maker_name': 'Sara Whitfield',
                'email_address': 'sara@whitfieldlaw.com',
                'email_spelled_back_confirmed': True}}}}
        cur.execute("""INSERT INTO webhook_events (call_id, event, payload)
                       VALUES ('c1','call_analyzed',%s)""", (json.dumps(payload),))
    db.commit()
    drain.drain_once()
    row = _get(db, lid)
    assert row['has_confirmed_email'] is True
    assert row['dm_email'] == 'sara@whitfieldlaw.com'


# --------------------------------------------------------------------------
# L2 never dials
# --------------------------------------------------------------------------

def test_l2_is_never_a_dial_candidate(db, cfg_env, enrolled):
    """At L2 we OWE them an email. Calling would ask what we are about to answer."""
    lid = _lead(db, has_confirmed_email=True, dm_email='s@w.com', dm_email_confirmed=True)
    enrolled(lid)
    assert dialer.select_and_claim(cfg_env, limit=10) == []


def test_only_l1_is_a_dial_candidate(db, cfg_env, enrolled):
    """
    STAGE_DIALABLE is L1 only. An L2 lead is one we OWE an email - dialing it
    again would talk to a firm we have already promised to write to.

    This used to construct an L3 lead. L3 stopped being a value the column
    accepted (migration 027), and the column is now a boolean entirely
    (migration 035), so unreachable states are refused by the database rather
    than filtered by the dialer - the stronger of the two. The lead that holds
    a confirmed email is the one that still exists and still must never be
    picked up.
    """
    owed = _lead(db, has_confirmed_email=False, phone_e164='+15552220011')
    sent = _lead(db, has_confirmed_email=True, phone_e164='+15552220012',
                 dm_name='Sara', dm_email='s@w.com', dm_email_confirmed=True)
    enrolled(owed); enrolled(sent)
    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(owed) in picked, 'the premise: a lead with no confirmed email dials'
    assert str(sent) not in picked, \
        'a firm we hold a confirmed email for was dialed - we owe it a send'


# --------------------------------------------------------------------------
# "I emailed them": L2 -> L3
# --------------------------------------------------------------------------

def test_mark_emailed_records_the_send_and_schedules_nothing(db, cfg_env):
    """
    THE SEAM IS KEPT, the automatic follow-up is not.

    Knowing an email went out and WHEN is worth having on its own: the
    follow-up column reads it, and click tracking computes "47m after send"
    from this exact timestamp.
    """
    lid = _lead(db, has_confirmed_email=True, dm_email='s@w.com', dm_email_confirmed=True)
    before = datetime.datetime.now(datetime.UTC)
    row = stages.mark_emailed(lid, emailed_by='operator')
    assert row is not None
    assert row['emailed_by'] == 'operator'
    assert row['emailed_at'] is not None and row['emailed_at'] >= before
    assert row['has_confirmed_email'] is True, 'the lead waits at L2; nothing advances it'
    assert row['next_attempt_at'] == _get(db, lid)['next_attempt_at'], \
        'marking a send must not schedule anything'


def test_clicking_twice_does_not_restamp_the_send_time(db, cfg_env):
    """
    A double click, or a sender racing the button, must be a no-op.

    THE FIRST SEND IS THE ONE THE TIMINGS ARE MEASURED FROM. Overwriting
    emailed_at would silently change every "N minutes after send" already
    recorded against this lead.
    """
    lid = _lead(db, has_confirmed_email=True, dm_email='s@w.com', dm_email_confirmed=True)
    first = stages.mark_emailed(lid, emailed_by='operator')
    second = stages.mark_emailed(lid, emailed_by='operator')
    assert second is None
    assert _get(db, lid)['emailed_at'] == first['emailed_at']


def test_the_automation_slots_in_through_the_same_function(db, cfg_env):
    """
    The point of the seam: the sequencer is another CALLER, not another
    implementation. Same function, different emailed_by.
    """
    lid = _lead(db, has_confirmed_email=True, dm_email='s@w.com', dm_email_confirmed=True)
    row = stages.mark_emailed(lid, emailed_by='auto:demandcounselor.com')
    assert row['emailed_at'] is not None
    assert row['emailed_by'] == 'auto:demandcounselor.com'


def test_the_web_handler_does_not_contain_the_transition(db):
    """If the button grows its own UPDATE, the automation becomes a rewrite."""
    import inspect
    from api import web
    src = inspect.getsource(web.lead_emailed)
    assert 'stages.mark_emailed' in src
    assert 'UPDATE leads' not in src


def test_only_l2_can_be_marked_emailed(db, cfg_env):
    """
    And it RAISES rather than returning None, so the caller can tell "not at
    L2" from "already sent". Collapsing the two reported a lead stuck at L1 as
    an email that had already gone out - which is what happened on the first
    real Send now.
    """
    import pytest as _pytest
    # There is exactly ONE other state now that the column is a boolean, so
    # this is exhaustive rather than a sample.
    lid = _lead(db, has_confirmed_email=False, phone_e164='+15552220049')
    with _pytest.raises(stages.NotAtL2) as e:
        stages.mark_emailed(lid, emailed_by='operator')
    assert 'L1' in str(e.value), \
        'the error must say which state the lead is in, via stages.stage_label()'


# --------------------------------------------------------------------------
# a reply stops the follow-up dead
# --------------------------------------------------------------------------

def test_a_reply_removes_the_lead_from_the_dialer(db, cfg_env, enrolled):
    # L1: the guard is stage-independent, and L3 is no longer dialable at all.
    lid = _lead(db, has_confirmed_email=False, dm_name='Sara', dm_email='s@w.com')
    enrolled(lid)
    assert len(dialer.select_and_claim(cfg_env, limit=10)) == 1

    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='new' WHERE lead_id=%s", (lid,))
    db.commit()
    assert stages.record_reply(lid) is True
    assert dialer.select_and_claim(cfg_env, limit=10) == []


def test_replied_at_alone_stops_the_dialer(db, cfg_env, enrolled):
    """
    THE TEST THAT ISOLATES REPLIED_GUARD.

    record_reply() also sets status='completed', and the status filter alone
    already excludes that - so a test going through record_reply() cannot tell
    whether REPLIED_GUARD does anything. The break pass caught exactly that.

    This is also the realistic shape for the coming sequencer: it detects a
    reply and stamps replied_at. Whether it also moves status is its business,
    and the dialer must not depend on it doing so.
    """
    # L1: REPLIED_GUARD is stage-independent, and an L3 lead is now excluded by
    # STAGE_DIALABLE anyway - which would mask the guard exactly as status did.
    lid = _lead(db, has_confirmed_email=False, dm_name='Sara', dm_email='s@w.com')
    enrolled(lid)
    assert len(dialer.select_and_claim(cfg_env, limit=10)) == 1

    with db.cursor() as cur:
        # replied_at set, status left DIALABLE - only REPLIED_GUARD can stop it
        cur.execute("""UPDATE leads SET replied_at = now(), status = 'new'
                        WHERE lead_id = %s""", (lid,))
    db.commit()
    assert dialer.select_and_claim(cfg_env, limit=10) == [], \
        'a lead that replied must not be dialed even while its status is dialable'


def test_recording_a_reply_twice_is_a_no_op(db, cfg_env):
    lid = _lead(db, has_confirmed_email=True)
    assert stages.record_reply(lid) is True
    assert stages.record_reply(lid) is False


# --------------------------------------------------------------------------
# the right agent and the right opener at each step
# --------------------------------------------------------------------------

def test_each_stage_uses_its_own_agent(cfg_env):
    # A campaign is passed explicitly: the version has exactly one home now,
    # and resolving the agent ID must not depend on which campaign is running.
    assert retell.agent_for(cfg_env, 'L1', {'agent_l1_version': 9})[0] == cfg_env.AGENT_L1
    # L3 HAS NO DIALING AGENT any more - a follow-up is its own campaign, by
    # email. agent_for refuses rather than guessing, which is the fail-closed
    # behaviour every unknown stage gets.
    import pytest as _pytest
    with _pytest.raises(ValueError):
        retell.agent_for(cfg_env, 'L3', {'agent_l1_version': 9})


def test_l2_has_no_agent_at_all(cfg_env):
    with pytest.raises(ValueError):
        retell.agent_for(cfg_env, 'L2', {'agent_l1_version': 9})


def test_dynamic_vars_supply_every_variable_the_l3_prompt_uses():
    """
    The bug this prevents: the L1 prompt has referenced {{company}} since it
    was written and the dialer never passed it, so the agent saw a literal
    placeholder. Any {{var}} in a shipped prompt must be supplied.
    """
    prompt = open('api/prompts/l3.txt').read()
    used = set(re.findall(r'\{\{(\w+)\}\}', prompt))
    supplied = set(retell.dynamic_vars(
        {'company': 'X', 'dm_name': 'Sara', 'dm_title': 'Intake',
         'dm_email': 's@w.com', 'emailed_at': None, 'lead_id': 'x'}))
    assert used, 'the L3 prompt should use dynamic variables'
    assert used <= supplied, f'unsupplied: {sorted(used - supplied)}'


def test_company_is_always_supplied_even_when_blank():
    v = retell.dynamic_vars({'company': None, 'lead_id': 'x'})
    assert v['company'] and '{{' not in v['company']


def test_the_l3_opener_uses_the_name(db, cfg_env, enrolled, monkeypatch):
    """A follow-up must not re-ask what the receptionist already answered."""
    captured = {}

    class R:
        call_id = 'call_fake_l3'

    def fake(cfg, to_number, lead, dynamic=None):
        captured['lead'] = lead
        captured['dynamic'] = dynamic
        return R()
    monkeypatch.setattr('api.retell.create_phone_call', fake)
    monkeypatch.setenv('DIAL_MODE', 'unrestricted')
    from api.config import load_config
    cfg = load_config()

    lid = _lead(db, has_confirmed_email=False, company='Whitfield Law', dm_name='Sara',
                dm_title='Intake Manager', dm_email='sara@whitfieldlaw.com')
    enrolled(lid)
    claimed = dialer.select_and_claim(cfg, limit=5)
    dialer.dial_one(cfg, claimed[0])

    d = captured['dynamic']
    assert d['dm_name'] == 'Sara'
    assert d['company'] == 'Whitfield Law'
    assert d['dm_email'] == 'sara@whitfieldlaw.com'
    assert d['dm_title_suffix'] == ', Intake Manager'
    assert captured['lead']['has_confirmed_email'] is False


# --------------------------------------------------------------------------
# the whole walk
# --------------------------------------------------------------------------

def test_a_lead_walks_l1_to_l2_and_stops(db, cfg_env):
    """The ladder ENDS at L2. A follow-up is its own campaign now, started by
    a person - not a third rung that schedules itself."""
    lid = _lead(db)
    assert _get(db, lid)['has_confirmed_email'] is False

    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET dm_email='sara@whitfieldlaw.com',
                              dm_email_confirmed=true WHERE lead_id=%s""", (lid,))
        stages.advance_to_l2(cur, lid)
    db.commit()
    assert _get(db, lid)['has_confirmed_email'] is True

    stages.mark_emailed(lid, emailed_by='operator')
    final = _get(db, lid)
    assert final['has_confirmed_email'] is True, 'nothing advances past L2'
    assert final['emailed_at'] is not None, 'but we DO know it was emailed'

    with db.cursor() as cur:
        cur.execute("""SELECT summary FROM activity WHERE lead_id=%s
                        ORDER BY created_at""", (lid,))
        trail = [r['summary'] for r in cur.fetchall()]
    assert any('L1 -> L2' in t for t in trail)
    assert any('emailed' in t for t in trail)


# --------------------------------------------------------------------------
# the column is a BOOLEAN, so the type is the constraint
# --------------------------------------------------------------------------
def test_the_confirmed_email_flag_refuses_every_value_that_is_not_a_boolean(db):
    """
    THE DATABASE IS THE GUARD, not the dialer's WHERE clause.

    This used to be a CHECK on `stage`, and the history is worth keeping: the
    constraint allowed L1, L2, L3, L4, won and lost. Only api/stages.py wrote
    the column and it only ever wrote 'L2', so four of those six were states no
    code path could reach - but a hand-run UPDATE, a migration or a fixture
    could, and then STAGE_DIALABLE silently skipped the lead forever with
    nothing saying why. 'won' and 'lost' were worse: they are STATUSES, so
    status and stage could disagree about whether a lead was won with nothing
    deciding which was right.

    Migration 035 made it `has_confirmed_email boolean NOT NULL`, which is
    STRONGER than any CHECK: an unrepresentable state cannot be written at all,
    by anything, and there is no list of allowed values to fall out of step
    with the code. That is the same reasoning migration 027 used when it
    narrowed the CHECK - carried to its end.

    NOT NULL matters as much as the type. A NULL would be neither true nor
    false, and "AND NOT l.has_confirmed_email" would silently drop the row -
    the exact failure mode the old over-wide CHECK had.
    """
    import psycopg2
    for bad in ('L1', 'L2', 'won', 'maybe', '2', None):
        with db.cursor() as cur:
            cur.execute('SAVEPOINT s')
            try:
                cur.execute(
                    """INSERT INTO leads (company, phone_e164, timezone,
                                          has_confirmed_email)
                       VALUES ('Whitfield Law', '+15552229999', %s, %s)""",
                    (LA, bad))
            except (psycopg2.errors.InvalidTextRepresentation,
                    psycopg2.errors.NotNullViolation,
                    psycopg2.errors.DatatypeMismatch):
                cur.execute('ROLLBACK TO SAVEPOINT s')
            else:
                cur.execute('ROLLBACK TO SAVEPOINT s')
                raise AssertionError(
                    f'has_confirmed_email={bad!r} was accepted - the column '
                    f'must hold true or false and nothing else, or a row can '
                    f'sit unreachable behind STAGE_DIALABLE')
    db.rollback()


def test_the_L1_L2_label_is_translated_in_exactly_one_place(db):
    """
    The 'L1'/'L2' vocabulary is still true of things that are NOT the lead's
    current state: which agent Retell should run, and the stage recorded on a
    past call or score. stages.stage_label() is the single translation.

    Scattering `'L2' if x else 'L1'` is how two vocabularies drift and how
    somebody eventually writes a third.
    """
    assert stages.stage_label(True) == 'L2'
    assert stages.stage_label(False) == 'L1'
    assert stages.stage_label(None) == 'L1', 'unknown must not read as confirmed'
    assert stages.stage_label({'has_confirmed_email': True}) == 'L2'
    assert stages.stage_label({'has_confirmed_email': False}) == 'L1'
    assert stages.stage_label({}) == 'L1', 'a row missing the key is not confirmed'

    # And no module outside api/stages.py may build the label itself.
    import glob
    import os
    api_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'api')
    offenders = []
    for f in glob.glob(os.path.join(api_dir, '*.py')):
        if os.path.basename(f) == 'stages.py':
            continue
        body = open(f).read()
        if "'L2' if" in body or '"L2" if' in body:
            offenders.append(os.path.basename(f))
    assert not offenders, (
        f'{offenders} build the L1/L2 label inline - call stages.stage_label() '
        f'so there is one place the two vocabularies meet')


def test_both_states_round_trip(db):
    """
    The other half. Replaces test_stage_still_accepts_the_two_that_exist, which
    asserted the old CHECK still allowed 'L1' and 'L2' - a boolean column
    cannot hold anything else, so the allowed-values half of that test is now
    the type system's job (see the type test above). What is still worth
    asserting is that both states survive a write and a read.
    """
    for flag in (False, True):
        lid = _lead(db, has_confirmed_email=flag,
                    phone_e164='+1555222%04d' % (50 + int(flag)))
        assert _get(db, lid)['has_confirmed_email'] is flag
