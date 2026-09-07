"""
Immediate alerts. NOT the digest.

The digest is once a day and batched on purpose. These are the two things
that cannot wait for it:

  needs_human    a call a person has to listen to now
  demo_pending   PHASE 6. A verbal yes decays - the target is an invite inside
                 15 minutes. The PATH is wired here now so it is not invented
                 under time pressure the first time L4 books something.

Nothing in phase 3 sets status='demo_pending'; L4 does, in phase 6. The
scanner below already picks it up the moment it appears.
"""

from api import db, mail

KINDS = ('demo_pending', 'needs_human')


def scan(cfg) -> int:
    """Raise an alert row for any lead sitting in a state that needs a person."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # PHASE 6 path, live now: a verbal yes with no invite sent.
            cur.execute(
                """INSERT INTO alerts (lead_id, kind, summary, detail)
                   SELECT l.lead_id, 'demo_pending',
                          l.company || ' agreed to a demo - SEND THE INVITE',
                          'demo_when=' || COALESCE(l.demo_when::text, '?') ||
                          ' tz=' || COALESCE(l.demo_when_tz, '?') ||
                          ' email=' || COALESCE(l.demo_email, '?')
                     FROM leads l
                    WHERE l.status = 'demo_pending'
                      AND l.invite_sent_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM alerts a
                                       WHERE a.lead_id = l.lead_id
                                         AND a.kind = 'demo_pending')""")
            return cur.rowcount


def send_pending(cfg, limit: int = 20) -> dict:
    sent, failed = 0, 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, summary, detail FROM alerts
                    WHERE sent_at IS NULL ORDER BY created_at LIMIT %s""", (limit,))
            rows = cur.fetchall()

    for r in rows:
        prefix = 'DEMO BOOKED' if r['kind'] == 'demo_pending' else 'Needs you'
        result = mail.send(cfg, cfg.DIGEST_TO,
                           f"[caller] {prefix}: {r['summary']}",
                           f"{r['summary']}\n\n{r['detail'] or ''}\n")
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if result['ok']:
                    cur.execute('UPDATE alerts SET sent_at=now() WHERE id=%s', (r['id'],))
                    sent += 1
                else:
                    cur.execute('UPDATE alerts SET send_error=%s WHERE id=%s',
                                (result['detail'][:2000], r['id']))
                    failed += 1
    return {'sent': sent, 'failed': failed}
