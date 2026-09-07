"""
The stage ladder: L1 -> L2 -> L3.

  L1  cold call the front desk. Goal: a name and a confirmed email.
  L2  we have the email and OWE them a send. No calling happens at L2.
  L3  we emailed them; call back in 3 days, warm, using the name.

THE SEAM FOR THE COMING AUTOMATION IS mark_emailed().

L2 is manual today: a person clicks "I emailed them" on the lead detail. The
sequenced sender from demandcounselor.com will fire ~15 minutes after a
confirmed capture and call this SAME function with emailed_by='auto:<domain>'.
Nothing else needs to change - the button and the sender are two callers of
one transition, not two implementations of it.

That is why the transition is here and not in the web handler.
"""

import datetime

from api import db

FOLLOWUP_DAYS = 3


def advance_to_l2(cur, lead_id, source: str = 'confirmed email captured'):
    """
    L1 -> L2 the moment a CONFIRMED email lands.

    Takes a cursor, not a connection: this runs inside the drain's existing
    transaction so the stage move and the email capture commit together. A
    lead that is at L2 with no email, or has an email but is still at L1, is
    a state nobody can reason about.
    """
    cur.execute(
        """UPDATE leads
              SET stage = 'L2', stage_changed_at = now(), updated_at = now()
            WHERE lead_id = %s
              AND stage = 'L1'
              AND dm_email IS NOT NULL
              AND dm_email_confirmed IS TRUE
            RETURNING lead_id""", (lead_id,))
    if cur.fetchone() is None:
        return False
    cur.execute(
        """INSERT INTO activity (lead_id, kind, stage, summary, detail)
           VALUES (%s, 'stage_change', 'L2', 'L1 -> L2', %s)""",
        (lead_id, source))
    return True


def mark_emailed(lead_id, emailed_by: str, when=None):
    """
    L2 -> L3. The follow-up call is due FOLLOWUP_DAYS later.

    Called by the "I emailed them" button today and by the sequencer later.
    emailed_by records which: an operator name, or 'auto:<domain>'.

    Returns the updated lead, or None if the lead was not at L2 - clicking
    twice, or a sender racing the button, must not push a lead to L3 twice or
    reset a follow-up that is already scheduled.
    """
    when = when or datetime.datetime.now(datetime.UTC)
    due = when + datetime.timedelta(days=FOLLOWUP_DAYS)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET stage = 'L3', status = 'new',
                          emailed_at = %s, emailed_by = %s,
                          next_attempt_at = %s,
                          stage_attempts = 0,
                          stage_changed_at = now(), updated_at = now()
                    WHERE lead_id = %s AND stage = 'L2'
                    RETURNING *""",
                (when, emailed_by, due, lead_id))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                """INSERT INTO activity (lead_id, kind, stage, summary, detail)
                   VALUES (%s, 'email_sent', 'L3', 'L2 -> L3: emailed, follow-up queued', %s)""",
                (lead_id, f'by {emailed_by}; follow-up due {due:%Y-%m-%d %H:%M} UTC'))
            return row


def record_reply(lead_id, when=None):
    """
    They replied. STOP CALLING.

    Not wired to anything yet - the sender owns reply detection. It exists so
    the stop path is one call rather than something invented later under
    pressure, and so the dialer's guard has a matching writer.
    """
    when = when or datetime.datetime.now(datetime.UTC)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads SET replied_at = %s, status = 'completed',
                          updated_at = now()
                    WHERE lead_id = %s AND replied_at IS NULL
                    RETURNING lead_id""", (when, lead_id))
            if cur.fetchone() is None:
                return False
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary)
                   VALUES (%s, 'note', 'they replied to the email - follow-up call cancelled')""",
                (lead_id,))
            return True
