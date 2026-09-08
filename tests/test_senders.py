"""
THE FROM-ADDRESS IS A CHOICE, NOT A TEXT FIELD.

Brevo only delivers from a sender verified on the account, so free text let you
save a config that cannot send. The from-NAME stays free text - Brevo does not
verify display names.
"""

import pytest

from fastapi.testclient import TestClient

from api import campaigns as c, senders


@pytest.fixture
def client(db, cfg_env):
    import api.web as web            # noqa: F401 - registers the routes
    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_brevo(monkeypatch):
    """Never call Brevo from the suite. The live list is exercised by hand;
    what these tests own is the behaviour AROUND it."""
    senders._cache.update(at=0.0, senders=None, error=None)
    monkeypatch.setattr(senders, '_fetch',
                        lambda cfg, timeout=10: [('noreply@counselorai.io', 'CounselorAI'),
                                                 ('sean@demandcounselor.com', 'Sean')])
    yield
    senders._cache.update(at=0.0, senders=None, error=None)


def test_the_default_is_info_at_counselorai(db, cfg_env):
    assert senders.DEFAULT_SENDER == 'info@counselorai.io'
    assert c.create('SND-default')['sender_email'] == 'info@counselorai.io'


def test_both_offered_addresses_are_always_in_the_list(db, cfg_env):
    """Offered whether or not Brevo has caught up. A domain still warming must
    be selectable, or the screen cannot express the intended config."""
    opts, _ = senders.options(cfg_env)
    emails = [o['email'] for o in opts]
    assert emails[0] == 'info@counselorai.io', 'the default comes first'
    assert 'sean@demandcounselor.com' in emails


def test_an_unverified_offer_is_labelled_not_hidden(db, cfg_env):
    """Hiding it would make the screen lie about what is configured."""
    opts, _ = senders.options(cfg_env)
    by_email = {o['email']: o for o in opts}
    assert by_email['info@counselorai.io']['verified'] is False
    assert by_email['sean@demandcounselor.com']['verified'] is True


def test_a_current_sender_outside_the_list_is_still_shown(db, cfg_env):
    """An existing config is never silently rewritten by rendering a page."""
    opts, _ = senders.options(cfg_env, current='legacy@old.example')
    row = [o for o in opts if o['email'] == 'legacy@old.example']
    assert row and row[0]['current'] and not row[0]['verified']


def test_a_typo_cannot_be_saved(db, cfg_env):
    assert senders.is_allowed(cfg_env, 'info@counselorai.io') is True
    assert senders.is_allowed(cfg_env, 'sean@demandcounselor.com') is True
    assert senders.is_allowed(cfg_env, 'noreply@counselorai.io') is True   # verified
    assert senders.is_allowed(cfg_env, 'sean@counselorai.oi') is False     # typo
    assert senders.is_allowed(cfg_env, '') is False


def test_brevo_being_down_does_not_break_the_screen(db, cfg_env, monkeypatch):
    """A campaign screen that will not load because Brevo is slow is worse than
    one showing the offered pair with a note saying the list is stale."""
    senders._cache.update(at=0.0, senders=None, error=None)
    def boom(cfg, timeout=10):
        raise OSError('brevo unreachable')
    monkeypatch.setattr(senders, '_fetch', boom)
    opts, err = senders.options(cfg_env)
    assert err and 'brevo unreachable' in err
    assert [o['email'] for o in opts][:2] == ['info@counselorai.io',
                                              'sean@demandcounselor.com']


def test_the_save_route_rejects_an_unlisted_sender(client, db, cfg_env):
    row = c.create('SND-route')
    cid = row['campaign_id']
    form = {'name': 'SND-route', 'notes': '', 'agent_l1_version': 9,
            'sender_name': 'Sean',
            'sender_company_line': 'CounselorAI LLC', 'daily_cap': 100,
            'max_concurrent': 1, 'dial_interval_min': 210,
            'dial_interval_max': 300}

    r = client.post(f'/campaign/{cid}/save',
                    data={**form, 'sender_email': 'typo@nowhere.example'},
                    follow_redirects=False)
    assert 'REJECTED' in r.headers['location']
    assert c.get(cid)['sender_email'] == 'info@counselorai.io', 'unchanged'

    r = client.post(f'/campaign/{cid}/save',
                    data={**form, 'sender_email': 'sean@demandcounselor.com'},
                    follow_redirects=False)
    assert 'REJECTED' not in r.headers['location']
    assert c.get(cid)['sender_email'] == 'sean@demandcounselor.com'


def test_the_from_name_is_still_free_text(client, db, cfg_env):
    """Brevo verifies addresses, not display names."""
    cid = c.create('SND-name')['campaign_id']
    client.post(f'/campaign/{cid}/save',
                data={'name': 'SND-name', 'notes': '', 'agent_l1_version': 9,
                      'sender_email': 'info@counselorai.io',
                      'sender_name': 'Whoever I Like',
                      'sender_company_line': 'CounselorAI LLC',
                      'daily_cap': 100, 'max_concurrent': 1,
                      'dial_interval_min': 210, 'dial_interval_max': 300},
                follow_redirects=False)
    assert c.get(cid)['sender_name'] == 'Whoever I Like'
