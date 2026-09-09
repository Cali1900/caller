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
    The token of this lead's LATEST send, or None if nothing has gone out.

    ⚠️ ONE TOKEN PER SEND, NOT PER LEAD (migration 036). That is what makes a
    click attributable to the step that produced it: if step 1 pulls every click
    the follow-ups are noise, and if step 3 does the opener needs rewriting. A
    per-lead count cannot tell those apart, and per-lead was the shape until the
    drip made the question real.

    THIS IS EMAIL 1's TOKEN, and it is PREPARED LAZILY. Email 1's draft is
    rendered and STORED at capture time and break 60 pins that Copy and Send
    produce the same bytes, so the token has to exist before the send does. The
    email_sends row is therefore created here with sent_at NULL - "prepared" -
    and stamped when the email actually goes. A click before that records
    minutes_since_sent = NULL, which is correct: there is no send to measure
    from.

    Drip steps do NOT come through here. They call drip.record_send()
    explicitly, at the moment they go, so each step owns its own token.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT click_token FROM email_sends
                            WHERE lead_id = %s AND seq = 1
                              AND click_token IS NOT NULL
                            LIMIT 1""", (lead_id,))
            row = cur.fetchone()
            if row:
                return row['click_token']
            cur.execute('SELECT dm_email FROM leads WHERE lead_id = %s',
                        (lead_id,))
            lead = cur.fetchone()
            if lead is None:
                return None
            tok = secrets.token_urlsafe(TOKEN_BYTES)
            # ON CONFLICT: two draft generations racing must not mint two
            # tokens for one email, or the Copy button and the send disagree.
            cur.execute("""INSERT INTO email_sends
                               (lead_id, step_id, seq, to_email, click_token)
                           VALUES (%s, NULL, 1, %s, %s)
                           RETURNING click_token""",
                        (lead_id, lead['dm_email'] or '', tok))
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


def url_for_token(base_url: str, token: str) -> str:
    """
    The absolute tracked URL for a token we already hold.

    Used by the drip: the send row exists, its token is minted, and the copy is
    rendered against THAT token rather than looking one up by lead.
    """
    base = (base_url or '').strip().rstrip('/')
    if not base or not token:
        return None
    return f'{base}/c/{token}'


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

    ⚠️ minutes_since_sent IS MEASURED FROM THIS SEND, NOT FROM emailed_at.
    TWO DIFFERENT ANCHORS, and anyone reading one will assume the other:

      THE SCHEDULE anchors to leads.emailed_at - the first send. Every drip
      step's delay_days is measured from there so the sequence cannot drift.

      CLICK TIMING anchors to the send that was clicked. "clicked 47m after
      send" on step 3 has to mean 47 minutes after STEP 3 went out; measured
      from emailed_at it would report every later click as "11 days after
      send" - true of the sequence, useless about the email.

    Computed HERE and STORED, so it stays true whatever happens to the send row
    afterwards. NULL when there is no recorded send time: a click with no send
    is possible (a forwarded mail) and must not read as zero.
    """
    if not token:
        return None
    ip = _clean_ip(ip)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.send_id, s.lead_id, s.sent_at,
                                  l.click_destination
                             FROM email_sends s
                             JOIN leads l ON l.lead_id = s.lead_id
                            WHERE s.click_token = %s""", (token,))
            lead = cur.fetchone()
            if lead is None:
                return None
            cur.execute(
                """INSERT INTO email_clicks
                       (lead_id, send_id, minutes_since_sent, user_agent, ip)
                   VALUES (%s, %s,
                           CASE WHEN %s::timestamptz IS NULL THEN NULL
                                ELSE GREATEST(0, (EXTRACT(EPOCH FROM
                                     (now() - %s::timestamptz)) / 60)::int) END,
                           %s, %s)
                   RETURNING minutes_since_sent""",
                (lead['lead_id'], lead['send_id'], lead['sent_at'],
                 lead['sent_at'], (user_agent or '')[:500], ip))
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
