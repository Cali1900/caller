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

    The sweep therefore only ever writes to `leads` and `archived_contacts`.
    It does not DELETE from suppression or email_do_not_send, and it must
    never learn how.

THE RETURN CLEARS GATES AND KEEPS FACTS.

    A GATE decides whether something may happen next, and a stale gate is a
    lead that can never be worked again. A FACT is something we paid a call to
    learn, and six months does not make it untrue.

      cleared   has_confirmed_email blocks the dialer (STAGE_DIALABLE)
                emailed_at/_by      blocks mark_emailed() - it is write-once
                dm_email_confirmed  read by autosend.eligibility(),
                                    drafts.generate_for() and advance_to_l2().
                                    Not a fact about the firm: it records that
                                    WE verified the address, and that
                                    verification is stale at six months even
                                    when the address is not.

      kept      dm_email, dm_name, dm_title, website, gatekeeper_name,
                demands_per_month, notes, tags, first_dialed_at

    The lead comes back holding the address and needing it re-confirmed, which
    is what the next call is for: it either re-confirms the contact or updates
    it. Nothing auto-advances a returned lead to L2 - advance_to_l2() is only
    called on a fresh capture (drain) or a person ticking confirmed (web).

    replied_at IS CLEARED TOO (2026-09-09), for the same reason and after the
    same argument. dialer.REPLIED_GUARD is "AND l.replied_at IS NULL", so a
    lead that replied once, was archived and returned could never be dialed
    again - identical shape to the stage bug, in this same function.

    Six months on, a firm that said "not interested" is a legitimate prospect
    again; that is the whole premise of archive_reason='refused' having a
    return date at all. And it defeats NO exclusion list, because neither list
    is keyed on the lead: someone who asked to be REMOVED is held by
    suppression (phone) or email_do_not_send (address) and stays held.

    The FACT survives the gate: replied_at, reply_note and replied_by are
    snapshotted into archived_contacts, so "they told us no in March, and here
    is what they said" is still readable on the lead that just came back -
    which is exactly what a person needs before dialing it again.
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


# What the return is about to destroy, kept as history. NOT a gate: nothing
# reads archived_contacts to decide whether to dial or send. The email_clicks
# rows are NOT deleted - minutes_since_sent was computed and stored at click
# time, so those timings stay true after emailed_at is cleared.
_SNAPSHOT = """
    INSERT INTO archived_contacts
        (lead_id, company, dm_name, dm_email, dm_email_confirmed,
         emailed_at, emailed_by, click_count, first_click_minutes,
         replied_at, reply_note, replied_by,
         archive_reason, archived_at)
    SELECT l.lead_id, l.company, l.dm_name, l.dm_email, l.dm_email_confirmed,
           l.emailed_at, l.emailed_by,
           coalesce(c.n, 0), c.first_minutes,
           l.replied_at, l.reply_note, l.replied_by,
           l.archive_reason, l.archived_at
      FROM leads l
      LEFT JOIN (SELECT lead_id, count(*) AS n,
                        min(minutes_since_sent) AS first_minutes
                   FROM email_clicks GROUP BY lead_id) c
             ON c.lead_id = l.lead_id
     WHERE l.lead_id = ANY(%s::uuid[])
       -- Only worth a row if something actually happened worth remembering:
       -- a send, an address we hold, or a reply we are about to clear.
       AND (l.emailed_at IS NOT NULL OR l.dm_email IS NOT NULL
            OR l.replied_at IS NOT NULL)
"""

# The gates the return clears. Kept as ONE fragment used by both the sweep and
# unarchive(), because two copies of this list is how they drift apart.
_CLEAR_GATES = """
                          -- GATES, cleared. Each of these blocks the lead
                          -- FOREVER if it survives the return:
                          --   has_confirmed_email -> dialer.STAGE_DIALABLE
                          --   emailed_at -> mark_emailed() is write-once
                          --   dm_email_confirmed -> autosend, drafts, the
                          --                 confirmed-email transition
                          --   replied_at -> dialer.REPLIED_GUARD, and
                          --                 autosend exclusion 5
                          has_confirmed_email = false,
                          email_confirmed_at = NULL,
                          emailed_at = NULL,
                          emailed_by = NULL,
                          dm_email_confirmed = NULL,
                          replied_at = NULL,
                          reply_note = NULL,
                          replied_by = NULL,
                          -- FACTS are kept: dm_email, dm_name, dm_title,
                          -- website, gatekeeper_name, demands_per_month,
                          -- notes and tags. We paid a call to learn them.
"""


def _snapshot_contacts(cur, lead_ids) -> None:
    """Preserve the send record for these leads before the return clears it."""
    if not lead_ids:
        return
    cur.execute(_SNAPSHOT, ([str(i) for i in lead_ids],))


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
            # Claim the due rows FIRST, so the snapshot and the clear act on
            # exactly the same set and a second sweep cannot take them.
            cur.execute(
                """SELECT lead_id FROM leads
                    WHERE status = 'archived'
                      AND returns_at IS NOT NULL AND returns_at <= now()
                    ORDER BY returns_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED""", (limit,))
            claimed = [r['lead_id'] for r in cur.fetchall()]
            if not claimed:
                return {'returned': 0, 'reasons': []}

            # HISTORY BEFORE THE CLEAR. "We emailed this firm in September and
            # heard nothing" has to survive the return, or the reason the lead
            # was archived is unreconstructable the moment it comes back.
            _snapshot_contacts(cur, claimed)

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
                          returns_at = NULL,""" + _CLEAR_GATES + """
                          updated_at = now()
                    WHERE lead_id = ANY(%s::uuid[])
                RETURNING lead_id, archive_reason""",
                ([str(i) for i in claimed],))
            rows = cur.fetchall()
            for r in rows:
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s, 'archive', 'returned to the pool',
                               %s)""",
                    (r['lead_id'],
                     f'rested {RETURN_AFTER.days} days after being archived '
                     f'({r["archive_reason"]}). Back at L1 and dialable; the '
                     f'address is kept but needs re-confirming. The old send '
                     f'record is in archived_contacts. Suppression and the '
                     f'email do-not-send list are untouched.'))
            return {'returned': len(rows),
                    'reasons': sorted({r['archive_reason'] for r in rows})}


def contacts(lead_id):
    """
    Every send record this lead had before a return cleared it, newest first.

    HISTORY, NEVER A GATE. Nothing may read this to decide whether to dial or
    send - that is what suppression, email_do_not_send and the live row are
    for. It exists so "we emailed this firm in September and heard nothing"
    is still answerable after the lead comes back looking fresh.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT * FROM archived_contacts
                            WHERE lead_id = %s
                            ORDER BY returned_at DESC""", (lead_id,))
            return cur.fetchall()


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
            # Same order as the sweep: history first, then clear. A hand pull
            # and the nightly sweep must leave a lead in the SAME state, or
            # "why is this one different" becomes unanswerable.
            cur.execute("SELECT lead_id FROM leads WHERE lead_id = %s "
                        "AND status = 'archived' FOR UPDATE", (lead_id,))
            if cur.fetchone() is None:
                return None
            _snapshot_contacts(cur, [lead_id])
            cur.execute(
                """UPDATE leads
                      SET status = 'new', pool_status = 'pool',
                          campaign_id = NULL, attempts = 0,
                          next_attempt_at = now(),
                          archived_at = NULL, returns_at = NULL,""" + _CLEAR_GATES + """
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
                 f'was archived ({row["archive_reason"]}). Back at L1 and '
                 f'dialable; the address is kept but needs re-confirming. The '
                 f'old send record is in archived_contacts. Suppression and '
                 f'the email do-not-send list are untouched.'))
            return row
