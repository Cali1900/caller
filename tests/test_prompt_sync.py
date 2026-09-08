"""
THE LIST IS AUTO-SYNCED. CHOOSING WHICH VERSION IS LIVE IS NOT.

The picker sat eight versions behind Retell because the only sync was a button
nobody clicked. Pulling on page load fixes that - but a refresh of the LIST
must never move the live version, or v8-goes-live-by-accident happens again in
a new form.
"""

import pytest

from api import campaigns as campaigns_mod, prompts


class _FakeVersion:
    def __init__(self, version, ms, published=True):
        self.version = version
        self.last_modification_timestamp = ms
        self.is_published = published


def _fake_retell(monkeypatch, versions, counter):
    """A Retell whose detail calls are COUNTED, so 'incremental' is measured
    rather than asserted by reading the code."""
    class FakeAgent:
        def get_versions(self, agent_id):
            counter['list'] += 1
            return versions

        def retrieve(self, agent_id, version=None):
            counter['detail'] += 1
            return type('A', (), {'response_engine': {'llm_id': 'llm_1', 'version': 1}})()

    class FakeLLM:
        def retrieve(self, llm_id, version=None):
            counter['detail'] += 1
            return type('L', (), {'general_prompt': 'prompt text', 'model': 'gpt-4.1'})()

    class FakeRetell:
        def __init__(self, api_key=None):
            self.agent = FakeAgent()
            self.llm = FakeLLM()

    monkeypatch.setattr('retell.Retell', FakeRetell)
    return counter


def test_every_version_retell_has_ends_up_in_the_list(db, cfg_env, monkeypatch):
    counter = {'list': 0, 'detail': 0}
    _fake_retell(monkeypatch, [_FakeVersion(v, 1757000000000 + v) for v in range(17)],
                 counter)
    out = prompts.sync_versions(cfg_env, 'L1')
    assert out['versions'] == 17
    rows = prompts.listing(cfg_env, 'L1')
    assert [r['agent_version'] for r in rows] == list(range(16, -1, -1)), 'newest first'


def test_the_second_sync_fetches_nothing(db, cfg_env, monkeypatch):
    """
    INCREMENTAL, and measured. A full detail fetch is two API calls per
    version; seventeen versions meant thirty-four calls, which is why this only
    ever ran from a button.
    """
    counter = {'list': 0, 'detail': 0}
    vs = [_FakeVersion(v, 1757000000000 + v) for v in range(17)]
    _fake_retell(monkeypatch, vs, counter)

    prompts.sync_versions(cfg_env, 'L1')
    first = counter['detail']
    assert first == 34, f'17 versions x 2 detail calls, got {first}'

    counter['detail'] = 0
    prompts.sync_versions(cfg_env, 'L1')
    assert counter['detail'] == 0, 'nothing changed - nothing should be fetched'


def test_a_changed_version_is_re_fetched(db, cfg_env, monkeypatch):
    """Skipping is keyed on Retell's modification timestamp, not merely on the
    version existing."""
    counter = {'list': 0, 'detail': 0}
    vs = [_FakeVersion(1, 1757000000000)]
    _fake_retell(monkeypatch, vs, counter)
    prompts.sync_versions(cfg_env, 'L1')

    counter['detail'] = 0
    vs[0].last_modification_timestamp = 1757000999000      # edited in Retell
    prompts.sync_versions(cfg_env, 'L1')
    assert counter['detail'] == 2


def test_a_sync_never_changes_which_version_is_live(db, cfg_env, monkeypatch):
    """
    THE PROPERTY THAT MATTERS. v8 went live once because agent.update() applied
    to whatever draft was sitting in the dashboard. Refreshing the LIST must
    not be able to do the same thing by a different route.
    """
    counter = {'list': 0, 'detail': 0}
    _fake_retell(monkeypatch, [_FakeVersion(v, 1757000000000 + v) for v in range(17)],
                 counter)
    cid = campaigns_mod.create('SYNC-live', agent_l1_version=9)['campaign_id']
    prompts.sync_versions(cfg_env, 'L1')
    assert campaigns_mod.get(cid)['agent_l1_version'] == 9


def test_retell_being_down_does_not_break_the_page(db, cfg_env, monkeypatch):
    """A prompts page that will not load because Retell is slow is worse than a
    stale list with a note saying so."""
    class Boom:
        def __init__(self, api_key=None):
            raise OSError('retell unreachable')
    monkeypatch.setattr('retell.Retell', Boom)
    prompts._sync_cache.clear()
    err = prompts.sync_if_stale(cfg_env, 'L1')
    assert err and 'retell unreachable' in err


def test_the_stale_check_does_not_hammer_retell(db, cfg_env, monkeypatch):
    counter = {'list': 0, 'detail': 0}
    _fake_retell(monkeypatch, [_FakeVersion(1, 1757000000000)], counter)
    prompts._sync_cache.clear()
    prompts.sync_if_stale(cfg_env, 'L1')
    prompts.sync_if_stale(cfg_env, 'L1')
    prompts.sync_if_stale(cfg_env, 'L1')
    assert counter['list'] == 1, 'cached within SYNC_MAX_AGE'


def test_only_published_versions_are_offered(db, cfg_env, monkeypatch):
    """
    A draft in the Retell dashboard is somebody mid-edit, not something you can
    dial. Offering drafts is how v8 went live by accident: the list implied
    they were choosable.
    """
    counter = {'list': 0, 'detail': 0}
    _fake_retell(monkeypatch, [
        _FakeVersion(0, 1757000000000, published=True),
        _FakeVersion(1, 1757000000001, published=False),   # a draft
        _FakeVersion(2, 1757000000002, published=True),
    ], counter)
    prompts.sync_versions(cfg_env, 'L1')
    offered = [r['agent_version'] for r in prompts.listing(cfg_env, 'L1')]
    assert offered == [2, 0], f'the draft must not be offered, got {offered}'


def test_a_campaign_pinned_to_a_draft_still_sees_it(db, cfg_env, monkeypatch):
    """
    An existing config is never silently dropped off the screen it is edited
    on. If a campaign already points at an unpublished version, that version
    stays in the list - flagged - so the operator can see it and move off it.
    """
    counter = {'list': 0, 'detail': 0}
    _fake_retell(monkeypatch, [
        _FakeVersion(0, 1757000000000, published=True),
        _FakeVersion(1, 1757000000001, published=False),
    ], counter)
    prompts.sync_versions(cfg_env, 'L1')
    rows = prompts.listing(cfg_env, 'L1', include_version=1)
    by_ver = {r['agent_version']: r for r in rows}
    assert 1 in by_ver and by_ver[1]['is_published'] is False
