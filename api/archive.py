"""
ARCHIVE: the place a lead rests when nothing more should happen to it yet.

Every lead is worked by the machine, worked by Sean, or resting. Archive is
resting, and it TERMINATES - six months later the lead returns to the pool as
a fresh prospect, or a human decides otherwise.

THE REASON IS THE POINT. A firm that ran out of no-answers and a firm that
said no are the same row today and completely different prospects in March.
Archiving without the reason throws that away and nothing else on the lead
can reconstruct it.

⚠️  RETURNING A LEAD NEVER CLEARS AN EXCLUSION LIST.

    Suppression is keyed on the PHONE. email_do_not_send is keyed on the
    ADDRESS. Neither is keyed on the lead, and both outlive it - they survive
    the return, a re-upload of the same firm, and a dedup that merges two
    rows into one. A suppressed number coming back out of archive and being
    dialed is the failure this entire system exists to prevent.

    The sweep therefore only ever writes to `leads`. It does not DELETE from
    suppression or email_do_not_send, and it must never learn how.
"""

import datetime

from api import db

# 'refused' is a person saying no. 'no_reply' is silence after a whole drip.
# 'max_attempts' is the dialer giving up. They read alike in a status column
# and mean entirely different things six months out.
REASONS = ('max_attempts', 'refused', 'no_reply', 'bad_email',
           'unsubscribed', 'manual')

RETURN_AFTER = datetime.timedelta(days=182)      # ~6 months

# Archiving for these reasons is a fact about the ADDRESS, not about this
# lead's turn in the queue. The address goes on the do-not-send list, which is
# what still says "never" after the lead has been returned to the pool.
DO_NOT_SEND_REASONS = {'bad_email', 'unsubscribed'}


class ArchiveRefused(Exception):
    pass


def archive(lead_id, reason: str, by: str = 'system', note: str = ''):
    """
    Rest a lead. Returns the row, or raises.

    Idempotent on an already-archived lead: it returns the existing row
    rather than restamping archived_at, because restamping moves returns_at
    and a lead archived in March would silently rest until September.
    """
    if reason not in REASONS:
        raise ArchiveRefused(
            f'{reason!r} is not an archive reason. One of: {", ".join(REASONS)}. '
            f'The reason is the whole point of the archive - a lead resting '
            f'for an unnamed cause cannot be judged when it comes back.')

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT lead_id, status, dm_email, archived_at '
                        'FROM leads WHERE lead_id = %s FOR UPDATE', (lead_id,))
            row = cur.fetchone()
            if not row:
                raise ArchiveRefused(f'no lead {lead_id}')

            # The address goes on the list FIRST. If this transaction fails
            # afterwards the worst case is an address excluded from mail that
            # nobody is mailing yet; the reverse order risks an archived
            # bounce with nothing recording the bounce.
            if reason in DO_NOT_SEND_REASONS and row['dm_email']:
                cur.execute(
                    """INSERT INTO email_do_not_send (email, reason, source)
                       VALUES (lower(btrim(%s)), %s, %s)
                       ON CONFLICT (email) DO NOTHING""",
                    (row['dm_email'], reason, f'archive:{by}'))

            if row['archived_at'] is not None:
                return row

            cur.execute(
                """UPDATE leads
                      SET status = 'archived',
                          archived_at = now(),
                          archive_reason = %s,
                          returns_at = now() + %s,
                          pool_status = 'done',
                          updated_at = now()
                    WHERE lead_id = %s
                RETURNING lead_id, status, archive_reason, archived_at, returns_at""",
                (reason, RETURN_AFTER, lead_id))
            out = cur.fetchone()
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'archive', %s, %s)""",
                (lead_id, f'archived ({reason}) by {by}',
                 note or f'returns {out["returns_at"]:%Y-%m-%d}'))
            return out


def due(limit: int = 500):
    """Leads past returns_at. Read-only - the sweep is the only writer."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT lead_id, company, archive_reason, returns_at
                     FROM leads
                    WHERE status = 'archived' AND returns_at IS NOT NULL
                      AND returns_at <= now()
                    ORDER BY returns_at
                    LIMIT %s""", (limit,))
            return cur.fetchall()


def return_due(limit: int = 500) -> dict:
    """
    Return rested leads to the pool. The nightly sweep.

    ⚠️  THIS WRITES TO `leads` AND NOTHING ELSE. It does not touch
    suppression or email_do_not_send. Both are keyed on the phone and the
    address rather than the lead, both outlive it, and a lead coming back out
    of archive is not evidence that either should be forgotten.

    A returned lead is a FRESH prospect: no campaign, no attempts, no
    dialing history driving its next attempt. What it keeps is everything a
    person would want to read before dialing it again - the archive reason,
    the old activity, the scores.

    first_dialed_at is deliberately KEPT. It is the cohort key the funnel
    measures against, and clearing it would quietly rewrite a completed
    month's history to improve a future one. The cost is that a returned
    lead does not count against the daily NEW-lead cap; the alternative is
    losing a measurement that has already been reported.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET status = 'new',
                          pool_status = 'pool',
                          campaign_id = NULL,
                          attempts = 0,
                          -- Due immediately, like any fresh lead. The column
                          -- is NOT NULL and defaults to now(); nulling it is
                          -- not "no next attempt", it is a constraint error.
                          next_attempt_at = now(),
                          archived_at = NULL,
                          returns_at = NULL,
                          updated_at = now()
                    WHERE lead_id IN (
                        SELECT lead_id FROM leads
                         WHERE status = 'archived'
                           AND returns_at IS NOT NULL AND returns_at <= now()
                         ORDER BY returns_at
                         LIMIT %s
                         FOR UPDATE SKIP LOCKED)
                RETURNING lead_id, archive_reason""", (limit,))
            rows = cur.fetchall()
            for r in rows:
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s, 'archive', 'returned to the pool',
                               %s)""",
                    (r['lead_id'],
                     f'rested {RETURN_AFTER.days} days after being archived '
                     f'({r["archive_reason"]}). Suppression and the email '
                     f'do-not-send list are untouched.'))
            return {'returned': len(rows),
                    'reasons': sorted({r['archive_reason'] for r in rows})}


# ---------------------------------------------------------------------------
# the email do-not-send list
# ---------------------------------------------------------------------------

def do_not_send(email: str, reason: str, source: str = 'operator') -> bool:
    """Put an ADDRESS beyond reach. True if it was newly added."""
    addr = (email or '').strip().lower()
    if not addr:
        return False
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO email_do_not_send (email, reason, source)
                   VALUES (%s,%s,%s) ON CONFLICT (email) DO NOTHING
                RETURNING email""", (addr, reason, source))
            return cur.fetchone() is not None


def is_do_not_send(email: str) -> bool:
    addr = (email or '').strip().lower()
    if not addr:
        return False
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM email_do_not_send WHERE email = %s',
                        (addr,))
            return cur.fetchone() is not None


def unarchive(lead_id, by: str = 'operator'):
    """
    Pull a lead back by hand, before its six months are up.

    Same rule as the sweep, for the same reason: this writes to `leads` and
    nothing else. A person deciding to work a firm again is not a decision
    that the number may be dialed - suppression answers that, and it is not
    ours to overrule from this screen.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET status = 'new', pool_status = 'pool',
                          campaign_id = NULL, attempts = 0,
                          next_attempt_at = now(),
                          archived_at = NULL, returns_at = NULL,
                          updated_at = now()
                    WHERE lead_id = %s AND status = 'archived'
                RETURNING lead_id, archive_reason""", (lead_id,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'archive', %s, %s)""",
                (lead_id, f'returned from archive by {by}',
                 f'was archived ({row["archive_reason"]}). Suppression and '
                 f'the email do-not-send list are untouched.'))
            return row
