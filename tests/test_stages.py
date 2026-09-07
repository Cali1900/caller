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

LA = 'America/Los_Angeles'


def _lead(db, **kw):
    cols = {'company': 'Whitfield Law', 'phone_e164': '+15552220001',
            'timezone': LA, 'pool_status': 'active', 'status': 'new', 'stage': 'L1'}
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
    """A started campaign with the window out of the way."""
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    date = campaigns.campaign_date(cfg_env)
    campaigns.ensure(cfg_env, date); campaigns.start(cfg_env, date)

    def enrol(lid):
        with db.cursor() as cur:
            cur.execute("""INSERT INTO campaign_leads (campaign_date, lead_id, source)
                           VALUES (%s,%s,'fresh') ON CONFLICT DO NOTHING""", (date, lid))
        db.commit()
    return enrol


# --------------------------------------------------------------------------
# L1 -> L2 on a CONFIRMED email only
# --------------------------------------------------------------------------

def test_a_confirmed_email_advances_l1_to_l2(db, cfg_env):
    lid = _lead(db, dm_email='sara@whitfieldlaw.com', dm_email_confirmed=True)
    with db.cursor() as cur:
        assert stages.advance_to_l2(cur, lid) is True
    db.commit()
    assert _get(db, lid)['stage'] == 'L2'


def test_an_unconfirmed_email_does_not_advance(db, cfg_env):
    """A wrong email is a dead lead that looks live. It must not walk the ladder."""
    lid = _lead(db, dm_email='typo@whitfieldlaw.com', dm_email_confirmed=False)
    with db.cursor() as cur:
        assert stages.advance_to_l2(cur, lid) is False
    db.commit()
    assert _get(db, lid)['stage'] == 'L1'


def test_no_email_does_not_advance(db, cfg_env):
    lid = _lead(db)
    with db.cursor() as cur:
        assert stages.advance_to_l2(cur, lid) is False
    db.commit()
    assert _get(db, lid)['stage'] == 'L1'


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
    assert row['stage'] == 'L2'
    assert row['dm_email'] == 'sara@whitfieldlaw.com'


# --------------------------------------------------------------------------
# L2 never dials
# --------------------------------------------------------------------------

def test_l2_is_never_a_dial_candidate(db, cfg_env, enrolled):
    """At L2 we OWE them an email. Calling would ask what we are about to answer."""
    lid = _lead(db, stage='L2', dm_email='s@w.com', dm_email_confirmed=True)
    enrolled(lid)
    assert dialer.select_and_claim(cfg_env, limit=10) == []


def test_l1_and_l3_are_dial_candidates(db, cfg_env, enrolled):
    l1 = _lead(db, stage='L1', phone_e164='+15552220011')
    l3 = _lead(db, stage='L3', phone_e164='+15552220012',
               dm_name='Sara', dm_email='s@w.com', dm_email_confirmed=True)
    enrolled(l1); enrolled(l3)
    got = {c['stage'] for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert got == {'L1', 'L3'}


# --------------------------------------------------------------------------
# "I emailed them": L2 -> L3
# --------------------------------------------------------------------------

def test_mark_emailed_moves_l2_to_l3_and_queues_the_follow_up(db, cfg_env):
    lid = _lead(db, stage='L2', dm_email='s@w.com', dm_email_confirmed=True)
    row = stages.mark_emailed(lid, emailed_by='operator')
    assert row is not None
    assert row['stage'] == 'L3'
    assert row['emailed_by'] == 'operator'
    delta = row['next_attempt_at'] - datetime.datetime.now(datetime.UTC)
    assert datetime.timedelta(days=2, hours=20) < delta < datetime.timedelta(days=3, hours=4)


def test_clicking_twice_does_not_reset_the_follow_up(db, cfg_env):
    """A double click, or a sender racing the button, must be a no-op."""
    lid = _lead(db, stage='L2', dm_email='s@w.com', dm_email_confirmed=True)
    first = stages.mark_emailed(lid, emailed_by='operator')
    second = stages.mark_emailed(lid, emailed_by='operator')
    assert second is None
    assert _get(db, lid)['next_attempt_at'] == first['next_attempt_at']


def test_the_automation_slots_in_through_the_same_function(db, cfg_env):
    """
    The point of the seam: the sequencer is another CALLER, not another
    implementation. Same function, different emailed_by.
    """
    lid = _lead(db, stage='L2', dm_email='s@w.com', dm_email_confirmed=True)
    row = stages.mark_emailed(lid, emailed_by='auto:demandcounselor.com')
    assert row['stage'] == 'L3'
    assert row['emailed_by'] == 'auto:demandcounselor.com'


def test_the_web_handler_does_not_contain_the_transition(db):
    """If the button grows its own UPDATE, the automation becomes a rewrite."""
    import inspect
    from api import web
    src = inspect.getsource(web.lead_emailed)
    assert 'stages.mark_emailed' in src
    assert 'UPDATE leads' not in src


def test_only_l2_can_be_marked_emailed(db, cfg_env):
    for stage in ('L1', 'L3'):
        lid = _lead(db, stage=stage, phone_e164=f'+1555222{ord(stage[1]):04d}')
        assert stages.mark_emailed(lid, emailed_by='operator') is None


# --------------------------------------------------------------------------
# a reply stops the follow-up dead
# --------------------------------------------------------------------------

def test_a_reply_removes_the_lead_from_the_dialer(db, cfg_env, enrolled):
    lid = _lead(db, stage='L3', dm_name='Sara', dm_email='s@w.com')
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
    lid = _lead(db, stage='L3', dm_name='Sara', dm_email='s@w.com')
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
    lid = _lead(db, stage='L3')
    assert stages.record_reply(lid) is True
    assert stages.record_reply(lid) is False


# --------------------------------------------------------------------------
# the right agent and the right opener at each step
# --------------------------------------------------------------------------

def test_each_stage_uses_its_own_agent(cfg_env):
    assert retell.agent_for(cfg_env, 'L1')[0] == cfg_env.AGENT_L1
    assert retell.agent_for(cfg_env, 'L3')[0] == cfg_env.AGENT_L3
    assert cfg_env.AGENT_L1 != cfg_env.AGENT_L3


def test_l2_has_no_agent_at_all(cfg_env):
    with pytest.raises(ValueError):
        retell.agent_for(cfg_env, 'L2')


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

    lid = _lead(db, stage='L3', company='Whitfield Law', dm_name='Sara',
                dm_title='Intake Manager', dm_email='sara@whitfieldlaw.com')
    enrolled(lid)
    claimed = dialer.select_and_claim(cfg, limit=5)
    dialer.dial_one(cfg, claimed[0])

    d = captured['dynamic']
    assert d['dm_name'] == 'Sara'
    assert d['company'] == 'Whitfield Law'
    assert d['dm_email'] == 'sara@whitfieldlaw.com'
    assert d['dm_title_suffix'] == ', Intake Manager'
    assert captured['lead']['stage'] == 'L3'


# --------------------------------------------------------------------------
# the whole walk
# --------------------------------------------------------------------------

def test_a_lead_walks_l1_to_l2_to_l3(db, cfg_env):
    lid = _lead(db)
    assert _get(db, lid)['stage'] == 'L1'

    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET dm_email='sara@whitfieldlaw.com',
                              dm_email_confirmed=true WHERE lead_id=%s""", (lid,))
        stages.advance_to_l2(cur, lid)
    db.commit()
    assert _get(db, lid)['stage'] == 'L2'

    stages.mark_emailed(lid, emailed_by='operator')
    final = _get(db, lid)
    assert final['stage'] == 'L3'
    assert final['emailed_at'] is not None

    with db.cursor() as cur:
        cur.execute("""SELECT summary FROM activity WHERE lead_id=%s
                        ORDER BY created_at""", (lid,))
        trail = [r['summary'] for r in cur.fetchall()]
    assert any('L1 -> L2' in t for t in trail)
    assert any('L2 -> L3' in t for t in trail)
