"""
Which prompt is live is an OPERATOR CHOICE, not a side effect.

v8 became live because agent.update() applies to whatever draft is sitting in
the Retell dashboard. These tests pin the property that fixed: the live
version is DECLARED, and editing a draft cannot change what dials.

The declaration moved from global settings to the campaign - a version is
something a campaign points at, so two campaigns can run different prompts -
but the property under test is unchanged.
"""
import pytest
from api import campaigns as campaigns_mod, retell
from conftest import running_campaign_id


@pytest.fixture(autouse=True)
def fresh(db):
    yield


def test_the_live_version_comes_from_the_campaign_not_env(db, cfg_env):
    cid = running_campaign_id()
    campaigns_mod.update(cid, agent_l1_version=7)
    assert retell.agent_for(cfg_env, 'L1')[1] == 7
    campaigns_mod.update(cid, agent_l1_version=9)
    assert retell.agent_for(cfg_env, 'L1')[1] == 9


def test_only_l1_has_a_live_version(db, cfg_env):
    """
    L3 was descoped - a follow-up is its own campaign, by email - so there is
    exactly one live version and L3 has no dialing agent to point at.
    agent_for REFUSES rather than guessing, which is the same fail-closed
    behaviour any unknown stage gets.
    """
    cid = running_campaign_id()
    campaigns_mod.update(cid, agent_l1_version=9)
    assert retell.agent_for(cfg_env, 'L1')[1] == 9
    with pytest.raises(ValueError):
        retell.agent_for(cfg_env, 'L3')


def test_two_campaigns_can_point_at_different_versions(db, cfg_env):
    """A version is a property of a campaign, never the thing that
    identifies one."""
    a = campaigns_mod.create('PV-A', agent_l1_version=7)
    b = campaigns_mod.create('PV-B', agent_l1_version=9)
    assert retell.agent_for(cfg_env, 'L1', campaign=a)[1] == 7
    assert retell.agent_for(cfg_env, 'L1', campaign=b)[1] == 9


def test_it_refuses_to_dial_if_the_campaign_cannot_be_read(db, cfg_env, monkeypatch):
    """
    It used to fall back to an AGENT_L1_VERSION env seed here. That was the
    only moment the env copy was ever used, and it was the stale copy: the
    picker writes the campaign row and nothing wrote env.

    Dialing with a version nobody chose is the precise failure the picker
    exists to prevent - v8 went live once as a side effect of an unrelated
    edit, and no score could be attributed afterwards. Refusing loses the
    call; guessing loses the ability to trust every call that followed.
    """
    def boom():
        raise RuntimeError('db down')
    monkeypatch.setattr('api.db.get_conn', boom)
    with pytest.raises(RuntimeError):
        retell.agent_for(cfg_env, 'L1')


def test_it_refuses_when_no_campaign_is_running(db, cfg_env):
    """No campaign means nothing says which prompt is live. There is no
    default version, because a default is a second answer."""
    campaigns_mod.stop()
    with pytest.raises(ValueError) as e:
        retell.agent_for(cfg_env, 'L1')
    assert 'agent_l1_version' in str(e.value)


def test_the_dialed_version_is_stamped_into_call_metadata(db, cfg_env, monkeypatch):
    """calls.agent_version is how a score shift stays attributable."""
    captured = {}

    class R:
        call_id = 'c_meta'

    def fake(**kwargs):
        captured.update(kwargs)
        return R()
    monkeypatch.setattr('api.retell._client',
                        lambda cfg: type('C', (), {'call': type('X', (), {
                            'create_phone_call': staticmethod(fake)})()})())
    campaigns_mod.update(running_campaign_id(), agent_l1_version=9)
    retell.create_phone_call(cfg_env, '+15551230000',
                             {'lead_id': 'abc', 'stage': 'L1', 'company': 'X'})
    assert captured['override_agent_version'] == 9
    assert captured['metadata']['agent_version'] == 9
    assert captured['metadata']['stage'] == 'L1'


def test_an_out_of_range_version_is_refused(db):
    """
    Enforced by a DB CHECK now that settings.py is deleted - stronger than the
    Python validation it replaces, because a script or a stray UPDATE cannot
    get round it either.
    """
    import psycopg2
    cid = running_campaign_id()
    before = campaigns_mod.get(cid)['agent_l1_version']
    for bad in (-1, 10000):
        with pytest.raises(psycopg2.errors.CheckViolation):
            campaigns_mod.update(cid, agent_l1_version=bad)
    assert campaigns_mod.get(cid)['agent_l1_version'] == before
