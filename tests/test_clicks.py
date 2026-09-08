"""
CLICK TRACKING.

The endpoint is PUBLIC - a recipient's browser hits it - so most of what is
worth testing here is about what it must NOT do.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, clicks, db as dbm, drafts, stages


@pytest.fixture
def client(db, cfg_env):
    import api.web as web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _lead(db, email='bob@firm.example', body=None):
    cid = c.create('CLK-' + email[:5])['campaign_id']
    if body is not None:
        c.update(cid, body_with_name=body, body_without=body)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, stage)
                           VALUES (%s,'W','Bob Smith',%s,true,
                                   'America/Los_Angeles',%s,'L2')
                           RETURNING lead_id""",
                        ('+1424555' + str(abs(hash(email)) % 9000 + 1000), email, cid))
            return cur.fetchone()['lead_id']


BASE = 'https://caller-dev.counselorai.io'


# ---------------------------------------------------------------------------
# the rewrite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('link', [
    'https://counselorai.io/#letter',
    'https://counselorai.io/',
    'https://www.counselorai.io/samples/demand',
    'http://counselorai.io/#letter',
])
def test_any_counselorai_link_in_the_copy_is_rewritten(db, link):
    """
    THE BUG THIS EXISTS FOR. The first cut matched ONE hardcoded string. The
    copy is operator-editable and had already been changed, so the rewrite
    matched nothing and produced an untracked draft in silence.
    """
    lid = _lead(db, f'l{abs(hash(link))%999}@f.example')
    out = clicks.rewrite(f'See it here: {link} - worth a look?', BASE, lid)
    assert link not in out
    assert f'{BASE}/c/' in out


def test_the_link_it_replaced_becomes_the_redirect_target(db):
    """Editing the copy changes where the click lands. Nothing to keep in sync."""
    lid = _lead(db, 'dest@f.example')
    clicks.rewrite('go to https://counselorai.io/pricing now', BASE, lid)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT click_destination FROM leads WHERE lead_id=%s', (lid,))
            assert cur.fetchone()['click_destination'] == 'https://counselorai.io/pricing'


def test_no_base_url_leaves_a_plain_working_link(db):
    """
    Failing to track is recoverable. Sending a lawyer a dead link is not.
    """
    lid = _lead(db, 'plain@f.example')
    body = 'See https://counselorai.io/#letter'
    assert clicks.rewrite(body, '', lid) == body


def test_a_body_with_no_link_is_untouched(db):
    lid = _lead(db, 'nolink@f.example')
    assert clicks.rewrite('no links here', BASE, lid) == 'no links here'


def test_the_token_is_stable_for_a_lead(db):
    """One token per lead, not per email - the drip sends up to four."""
    lid = _lead(db, 'stable@f.example')
    assert clicks.token_for(lid) == clicks.token_for(lid)


def test_two_leads_never_share_a_token(db):
    a, b = _lead(db, 'a@f.example'), _lead(db, 'b@f.example')
    assert clicks.token_for(a) != clicks.token_for(b)


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------

def test_a_click_records_minutes_since_send(db, client):
    """THE NUMBER SEAN WANTS."""
    lid = _lead(db, 'mins@f.example')
    stages.mark_emailed(lid, emailed_by='operator',
                        when=datetime.datetime.now(datetime.UTC)
                        - datetime.timedelta(minutes=47))
    tok = clicks.token_for(lid)
    r = client.get(f'/c/{tok}', follow_redirects=False)
    assert r.status_code == 302
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT minutes_since_sent FROM email_clicks WHERE lead_id=%s',
                        (lid,))
            assert cur.fetchone()['minutes_since_sent'] == 47


def test_clicking_twice_records_two_clicks(db, client):
    """He may click twice. That is data, not a duplicate."""
    lid = _lead(db, 'twice@f.example')
    stages.mark_emailed(lid, emailed_by='operator')
    tok = clicks.token_for(lid)
    client.get(f'/c/{tok}', follow_redirects=False)
    client.get(f'/c/{tok}', follow_redirects=False)
    assert clicks.summary(lid)['clicks'] == 2


def test_a_click_with_no_recorded_send_is_null_not_zero(db, client):
    """A forwarded mail is a real possibility. Zero would read as 'instantly'."""
    lid = _lead(db, 'fwd@f.example')
    tok = clicks.token_for(lid)
    client.get(f'/c/{tok}', follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT minutes_since_sent FROM email_clicks WHERE lead_id=%s',
                        (lid,))
            assert cur.fetchone()['minutes_since_sent'] is None


def test_an_unknown_token_still_redirects_and_reveals_nothing(db, client):
    """
    A 404 would tell a scanner it guessed wrong, and a recipient whose link got
    mangled should still land on the page rather than see an error from us.
    """
    r = client.get('/c/not-a-real-token', follow_redirects=False)
    assert r.status_code == 302
    assert r.headers['location'] == clicks.DESTINATION
    assert r.content == b''


def test_the_endpoint_returns_a_redirect_and_nothing_else(db, client):
    """No body, no lead data, no signal about whether the token was real."""
    lid = _lead(db, 'quiet@f.example')
    tok = clicks.token_for(lid)
    r = client.get(f'/c/{tok}', follow_redirects=False)
    assert r.content == b''
    blob = repr(dict(r.headers)).lower()
    for leak in ('quiet@f.example', 'bob', str(lid)):
        assert leak.lower() not in blob


def test_the_url_carries_a_token_and_nothing_else(db):
    """Nothing enumerable, and nothing that says who was mailed."""
    lid = _lead(db, 'urlshape@f.example')
    url = clicks.tracked_url(BASE, lid)
    tail = url.split('/c/')[1]
    assert str(lid) not in url and 'urlshape' not in url
    assert len(tail) >= 20, 'a 128-bit token, not a counter'


def test_a_click_lands_on_the_timeline(db, client):
    lid = _lead(db, 'tl@f.example')
    stages.mark_emailed(lid, emailed_by='operator')
    client.get(f'/c/{clicks.token_for(lid)}', follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM activity
                            WHERE lead_id=%s AND kind='click'""", (lid,))
            assert cur.fetchone()['n'] == 1


def test_no_open_tracking_anywhere(db):
    """
    Apple Mail Privacy Protection pre-loads pixels, so an open fires whether or
    not a human looked. If a pixel ever appears, this is what says so.
    """
    import glob
    import re
    hits = []
    for path in glob.glob('/app/api/**/*.py', recursive=True) + \
            glob.glob('/app/api/templates/*.html'):
        src = open(path).read().lower()
        for pat in ('open_pixel', 'tracking_pixel', 'open_tracked', '1x1.gif',
                    'pixel.gif', '/o/'):
            if pat in src:
                hits.append(f'{path}: {pat}')
    assert not hits, f'open tracking appeared: {hits}'


@pytest.mark.parametrize('bad', ['testclient', 'not-an-ip', '999.999.999.999',
                                 '<script>', '', '1.2.3.4, 5.6.7.8'])
def test_a_garbage_forwarded_for_never_loses_the_click(db, client, bad):
    """
    X-Forwarded-For is CLIENT-CONTROLLED and the column is `inet`, so a garbage
    header used to fail the whole insert and lose the click silently. The click
    is the signal; the IP is context. Never lose the first to the second.
    """
    lid = _lead(db, f'ip{abs(hash(bad))%999}@f.example')
    stages.mark_emailed(lid, emailed_by='operator')
    tok = clicks.token_for(lid)
    r = client.get(f'/c/{tok}', headers={'x-forwarded-for': bad},
                   follow_redirects=False)
    assert r.status_code == 302
    assert clicks.summary(lid)['clicks'] == 1, f'click lost for XFF={bad!r}'


def test_a_real_forwarded_for_is_kept(db, client):
    lid = _lead(db, 'realip@f.example')
    stages.mark_emailed(lid, emailed_by='operator')
    client.get(f'/c/{clicks.token_for(lid)}',
               headers={'x-forwarded-for': '203.0.113.9'}, follow_redirects=False)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT ip FROM email_clicks WHERE lead_id=%s', (lid,))
            assert str(cur.fetchone()['ip']) == '203.0.113.9'
