"""
Which prompt is live is an OPERATOR CHOICE, not a side effect.

v8 became live because agent.update() applies to whatever draft is sitting in
the Retell dashboard. These tests pin the property that fixed: the live
version comes from settings, and editing a draft cannot change what dials.
"""
import pytest
from api import retell, settings as settings_mod


@pytest.fixture(autouse=True)
def fresh(db):
    settings_mod._cache.update(at=0.0, values=None)
    yield
    settings_mod._cache.update(at=0.0, values=None)


def test_the_live_version_comes_from_settings_not_env(db, cfg_env):
    settings_mod.set_many({'agent_l1_version': 7})
    assert retell.agent_for(cfg_env, 'L1')[1] == 7
    settings_mod.set_many({'agent_l1_version': 9})
    assert retell.agent_for(cfg_env, 'L1')[1] == 9


def test_each_stage_has_its_own_live_version(db, cfg_env):
    settings_mod.set_many({'agent_l1_version': 9, 'agent_l3_version': 1})
    assert retell.agent_for(cfg_env, 'L1')[1] == 9
    assert retell.agent_for(cfg_env, 'L3')[1] == 1


def test_it_falls_back_to_a_known_version_if_settings_cannot_be_read(db, cfg_env, monkeypatch):
    """Falling back to the env seed beats guessing at 'latest'."""
    def boom():
        raise RuntimeError('db down')
    monkeypatch.setattr('api.db.get_conn', boom)
    settings_mod._cache.update(at=0.0, values=None)
    agent_id, version = retell.agent_for(cfg_env, 'L1')
    assert agent_id == cfg_env.AGENT_L1
    assert isinstance(version, int)


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
    settings_mod.set_many({'agent_l1_version': 9})
    retell.create_phone_call(cfg_env, '+15551230000',
                             {'lead_id': 'abc', 'stage': 'L1', 'company': 'X'})
    assert captured['override_agent_version'] == 9
    assert captured['metadata']['agent_version'] == 9
    assert captured['metadata']['stage'] == 'L1'


def test_an_out_of_range_version_is_refused(db):
    assert settings_mod.set_many({'agent_l1_version': -1})['ok'] is False
    assert settings_mod.set_many({'agent_l1_version': 'abc'})['ok'] is False
