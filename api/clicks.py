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

# Fallback destination when a lead has none recorded. The real page, unchanged.
DESTINATION = 'https://counselorai.io/#letter'

TOKEN_BYTES = 16          # 128 bits - not guessable, short enough to read


def token_for(lead_id) -> str:
    """
    The lead's click token, generated once and reused.

    ONE TOKEN PER LEAD today. That is a CURRENT-STATE CHOICE, not a settled
    one, and the drip will have to change it.

    ⚠️ DRIP_ARCHIVE_BRIEF.md specifies clicks tracked PER STEP, and that design
    wins (decided 2026-09-09). Per-lead cannot answer the question the drip is
    built to ask: is step 1 pulling every click, or is step 3? If step 1 does,
    the follow-ups are noise; if step 3 does, the opener needs rewriting. Time
    since send cannot separate those - only attribution to the step that
    produced the click can, and a per-lead count has no step to attribute to.

    Per-lead is right for TODAY, where there is exactly one manual email per
    lead, and it keeps one property worth carrying forward: anyone holding a
    token cannot reason about a lead's other sends, because there are none.

    Moving to per-step means the token belongs to the SEND, not the lead -
    a schema change (leads.click_token is a single column), so it is parked
    with the rest of the drip rather than half-done. Do not read this function
    as a settled decision against the brief.
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
    """
    The absolute tracked URL, or None when there is no base to build it from.

    NO BASE MEANS NO URL. Without the guard this returned "/c/<token>" - a
    RELATIVE path, which is a working link on a web page and a dead one in an
    email. The caller falls back to the plain sample page, which is the whole
    point of the fallback.
    """
    base = (base_url or '').strip().rstrip('/')
    if not base:
        return None
    tok = token_for(lead_id)
    return f'{base}/c/{tok}' if tok else None


def set_destination(lead_id, url: str) -> None:
    """Remember where this lead's tracked link should land."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE leads SET click_destination = %s WHERE lead_id = %s',
                        (url, lead_id))


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
            # A click is engagement - the strongest signal short of a reply.
            from api import pipeline
            pipeline.advance(cur, lead['lead_id'], 'engaged', 'clicked the sample link')
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
