"""
CLICK TRACKING.

Sean sends every email by hand. The app rewrites the sample link in the draft
to a tracked URL, logs the click, and 302s to the real page - the recipient
sees no difference.

THE ENDPOINT IS PUBLIC. It sits on the same vhost as the Retell webhook because
a recipient's browser has to reach it. That shapes everything here:

  * the URL carries a TOKEN and nothing else - no lead id, no campaign, no
    email address, nothing enumerable and nothing that leaks who was mailed
  * an unknown token REDIRECTS ANYWAY. Returning 404 would tell a scanner it
    guessed wrong; more importantly a real recipient whose token was somehow
    mangled should still land on the page rather than see an error from us
  * it returns a redirect and NOTHING ELSE - no body, no lead data, no
    indication whether the token was real

NO OPEN TRACKING. Apple Mail Privacy Protection pre-loads pixels, so an open
fires whether or not a human looked. Clicks and replies only.
"""

import secrets

from api import db

import ipaddress
import re

# Fallback destination when a lead has none recorded. The real page, unchanged.
DESTINATION = 'https://counselorai.io/#letter'

# ANY counselorai.io link in the copy is the tracked one.
#
# The first cut matched one hardcoded string. The copy is OPERATOR-EDITABLE and
# had already been changed from '.../#letter' to '/', so the rewrite matched
# nothing and produced an untracked draft without a word of complaint. A
# constant that must agree with editable text will eventually disagree with it.
SAMPLE_LINK = re.compile(r'https?://(?:www\.)?counselorai\.io[^\s<>"\')]*')

TOKEN_BYTES = 16          # 128 bits - not guessable, short enough to read


def token_for(lead_id) -> str:
    """
    The lead's click token, generated once and reused.

    ONE TOKEN PER LEAD, not per email. The drip sends up to four; a click is a
    click, and which email it came from is answered by the timing. A per-email
    token would also mean anyone holding one could reason about the others.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT click_token FROM leads WHERE lead_id = %s',
                        (lead_id,))
            row = cur.fetchone()
            if row is None:
                return None
            if row['click_token']:
                return row['click_token']
            tok = secrets.token_urlsafe(TOKEN_BYTES)
            cur.execute("""UPDATE leads SET click_token = %s
                            WHERE lead_id = %s AND click_token IS NULL
                            RETURNING click_token""", (tok, lead_id))
            got = cur.fetchone()
            return got['click_token'] if got else tok


def tracked_url(base_url: str, lead_id) -> str:
    tok = token_for(lead_id)
    return f"{base_url.rstrip('/')}/c/{tok}" if tok else None


def set_destination(lead_id, url: str) -> None:
    """Remember where this lead's tracked link should land."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE leads SET click_destination = %s WHERE lead_id = %s',
                        (url, lead_id))


def rewrite(body: str, base_url: str, lead_id) -> str:
    """
    Swap the counselorai.io link for the tracked one, and remember where it
    pointed so the redirect lands exactly there.

    Returns the body UNCHANGED when there is no base URL or no link to rewrite.
    An unset PUBLIC_BASE_URL must produce a plain working link, never a tracked
    one pointing nowhere: failing to track is recoverable, sending a dead link
    to a lawyer is not.
    """
    body = body or ''
    if not base_url:
        return body
    found = SAMPLE_LINK.search(body)
    if not found:
        return body
    tracked = tracked_url(base_url, lead_id)
    if not tracked:
        return body
    set_destination(lead_id, found.group(0))
    return SAMPLE_LINK.sub(tracked, body)


def _clean_ip(value):
    """
    A valid IP, or None.

    X-Forwarded-For is CLIENT-CONTROLLED, and the column is `inet` - so a
    garbage header used to make the whole insert fail and the click vanish.
    The click is the signal; the IP is context. Never lose the first to the
    second. (Starlette's TestClient sends the literal 'testclient', which is
    how this surfaced.)
    """
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


def record(token: str, user_agent: str = '', ip: str = None):
    """
    Log a click. Returns the URL to redirect to, or None for an unknown token.

    NEVER RAISES for an unknown token - the caller redirects either way. A
    tracking failure must not turn into an error page for someone who did
    nothing wrong.

    minutes_since_sent is computed HERE and STORED. It is the number Sean
    actually wants, and storing it means it stays true even if anything about
    emailed_at ever changes. NULL when the lead has no emailed_at: a click with
    no recorded send is possible (a forwarded mail) and must not read as zero.
    """
    if not token:
        return None
    ip = _clean_ip(ip)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT lead_id, emailed_at, click_destination
                             FROM leads WHERE click_token = %s""", (token,))
            lead = cur.fetchone()
            if lead is None:
                return None
            cur.execute(
                """INSERT INTO email_clicks
                       (lead_id, minutes_since_sent, user_agent, ip)
                   VALUES (%s,
                           CASE WHEN %s::timestamptz IS NULL THEN NULL
                                ELSE GREATEST(0, (EXTRACT(EPOCH FROM
                                     (now() - %s::timestamptz)) / 60)::int) END,
                           %s, %s)
                   RETURNING minutes_since_sent""",
                (lead['lead_id'], lead['emailed_at'], lead['emailed_at'],
                 (user_agent or '')[:500], ip))
            mins = cur.fetchone()['minutes_since_sent']
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'click', 'clicked the sample link', %s)""",
                (lead['lead_id'],
                 f'{mins}m after send' if mins is not None
                 else 'no recorded send - forwarded, or sent outside the app'))
            return lead['click_destination'] or DESTINATION


def summary(lead_id):
    """{'clicks': n, 'first_minutes': m|None, 'last_at': ts|None} for one lead."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) AS clicks,
                          min(minutes_since_sent) AS first_minutes,
                          max(clicked_at) AS last_at
                     FROM email_clicks WHERE lead_id = %s""", (lead_id,))
            return dict(cur.fetchone())
