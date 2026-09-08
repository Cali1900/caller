"""
THE PROMPT VERSION PICKER.

It failed in the way UI fails: the backend was correct the whole time and the
panel still told Sean the wrong thing. v15 sat at the top with its radio
checked while v9 - the actually-live one - was scrolled out of sight at the
bottom, so the panel read as "v15 is live" when it was not. That is the exact
confusion the radios were introduced to remove.

So these tests are about what the PAGE says, not only what the database holds.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, db as dbm, prompts


@pytest.fixture
def client(db, cfg_env, monkeypatch):
    # Never call Retell from the suite; the sync has its own tests.
    monkeypatch.setattr(prompts, 'sync_if_stale', lambda *a, **k: None)
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


@pytest.fixture
def campaign_with_versions(db, cfg_env):
    # The REAL agent id. listing() filters on cfg.AGENT_L1, so a made-up one
    # renders an empty list - which is how the first version of this fixture
    # made a working picker look broken.
    cid = c.create('PICK')['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            for v in range(16):
                cur.execute(
                    """INSERT INTO prompt_versions
                          (stage, agent_id, agent_version, prompt_text, model,
                           prompt_chars, changed_by, change_note, is_published)
                       VALUES ('L1',%s,%s,'p','gpt-4.1',10,'t','',true)""",
                    (cfg_env.AGENT_L1, v))
    return cid


def _form(**over):
    base = {'name': 'PICK', 'notes': '', 'agent_l1_version': 9,
            'sender_email': 'info@counselorai.io', 'sender_name': 'Sean',
            'sender_company_line': 'CounselorAI LLC', 'daily_cap': 100,
            'max_concurrent': 1, 'dial_interval_min': 210,
            'dial_interval_max': 300}
    base.update(over)
    return base


def test_selecting_a_version_and_saving_switches_which_is_live(db, client,
                                                               campaign_with_versions):
    cid = campaign_with_versions
    c.update(cid, agent_l1_version=9)
    r = client.post(f'/campaign/{cid}/save', data=_form(agent_l1_version=15),
                    follow_redirects=False)
    assert r.status_code == 303
    assert 'REJECTED' not in r.headers['location']
    assert c.get(cid)['agent_l1_version'] == 15


def test_the_save_button_is_in_the_picker_panel(db, client, campaign_with_versions):
    """
    A button sixty lines below the control it saves is a button you cannot
    find. Sean could not, and reported the picker as broken.
    """
    body = client.get(f'/campaign/{campaign_with_versions}').text
    assert 'data-v=' in body, 'no versions rendered - the rest proves nothing'
    i = body.index('name="agent_l1_version"')
    j = body.index('Save prompt version')
    assert j > i, 'the save must come after the list it saves'
    assert body.count('Save prompt version') == 1
    # and it must be INSIDE the form that posts the version
    form = body[body.index('/save"'):body.index('</form>', body.index('/save"'))]
    assert 'Save prompt version' in form


def test_the_live_row_is_marked_and_checked(db, client, campaign_with_versions):
    cid = campaign_with_versions
    c.update(cid, agent_l1_version=9)
    body = client.get(f'/campaign/{cid}').text
    import re
    rows = re.findall(r'<tr data-v="(\d+)" class="([^"]*)">(.*?)</tr>', body, re.S)
    live = [v for v, cls, _ in rows if 'islive' in cls]
    checked = [v for v, _, b in rows if 'checked' in b]
    assert live == ['9'], f'exactly one row may be LIVE, got {live}'
    assert checked == ['9'], f'the checked radio must be the live one, got {checked}'


def test_the_page_scrolls_the_live_row_into_view(db, client, campaign_with_versions):
    """The complaint: the live row was out of sight at the bottom while a
    different row sat at the top looking selected."""
    body = client.get(f'/campaign/{campaign_with_versions}').text
    assert 'scrollIntoView' in body
    assert "querySelector('tr.islive')" in body


def test_a_refused_save_says_so_and_changes_nothing(db, client,
                                                    campaign_with_versions):
    """'If the save fails, say so rather than silently reverting.'"""
    cid = campaign_with_versions
    c.update(cid, agent_l1_version=15)
    r = client.post(f'/campaign/{cid}/save',
                    data=_form(agent_l1_version=99999), follow_redirects=False)
    loc = r.headers['location']
    assert 'REJECTED' in loc
    assert '99999' in loc, 'the message must name what was refused'
    assert 'check constraint' not in loc, 'not a wall of Postgres'
    assert c.get(cid)['agent_l1_version'] == 15, 'nothing may change'


def test_a_successful_save_names_the_version_that_went_live(db, client,
                                                            campaign_with_versions):
    cid = campaign_with_versions
    r = client.post(f'/campaign/{cid}/save', data=_form(agent_l1_version=14),
                    follow_redirects=False)
    assert 'v14' in r.headers['location']


def test_a_rejected_banner_is_visually_distinct(db, client, campaign_with_versions):
    body = client.get(f'/campaign/{campaign_with_versions}?msg=REJECTED:+nope').text
    assert 'class="banner warn"' in body
    body = client.get(f'/campaign/{campaign_with_versions}?msg=saved').text
    assert 'class="banner"' in body
