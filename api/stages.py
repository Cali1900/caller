"""
The stage ladder: L1 -> L2. It STOPS at L2.

  L1  cold call the front desk. Goal: a name and a confirmed email.
  L2  we have the email. Nothing is dialed from here - the lead waits.

L3 IS UNWIRED FROM THE APP (2026-09-08). There is no automatic follow-up call.
A campaign is already a named configuration with its own prompt and its own
leads, so a follow-up IS just another campaign: assign the leads you want
called back, and start it when you choose. That is cleaner than a hardcoded
ladder and the operator controls when it runs. The L3 agent still exists in
Retell; only the app's automatic scheduling is gone.

THE SEAM IS KEPT. mark_emailed() still records emailed_at and emailed_by - it
just no longer schedules a call. Knowing an email went out, and WHEN, is worth
having on its own: it is what the follow-up column reads, and click tracking
computes "47m after send" from that exact timestamp. Every status change after
a send hangs off it.

A person clicks "I emailed them"; a sequenced sender would call the SAME
function with emailed_by='auto:<domain>'. Two callers of one transition, not
two implementations of it - which is why it lives here and not in the web
handler.
"""

import datetime

from api import db

# L3's automatic follow-up is gone; a follow-up is its own campaign. Kept as
# a named number because the L3 prompt in Retell still says "a few days ago".
class NotAtL2(RuntimeError):
    """
    mark_emailed was called on a lead that has not reached L2.

    Distinct from "already sent", which returns None. Collapsing the two made
    a lead stuck at L1 report as an email that had already gone out.
    """


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
    RECORDS THAT AN EMAIL WENT OUT, AND WHEN. Schedules nothing.

    The lead stays at L2. It used to advance to L3 and queue a follow-up call
    FOLLOWUP_DAYS out; that automatic ladder is gone. A follow-up is its own
    campaign now - assign the leads and start it when you choose.

    The TIMESTAMP is the point of this function, not the stage change:
      * the follow-up column on the leads list reads it
      * click tracking computes "47m after send" from it
      * every status change after a send is anchored to it
    Losing it would mean not knowing whether a firm had been emailed at all.

    Called by the "I emailed them" button today, and by a sequenced sender
    later with emailed_by='auto:<domain>'. Two callers of one transition.

    Returns the updated lead, or None if it was not at L2 - clicking twice, or
    a sender racing the button, must not stamp a second send time over the
    first. The FIRST send is the one the timings are measured from.

    STATUS IS NOT TOUCHED. The email state of a lead is derived from
    emailed_at (see _EMAIL_STATE in web.py), so putting it in `status` too
    would be two sources for one fact - and the second one drifts. The stage
    does not move either: L2 is where a lead waits.
    """
    when = when or datetime.datetime.now(datetime.UTC)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET emailed_at = %s, emailed_by = %s,
                          updated_at = now()
                    WHERE lead_id = %s AND stage = 'L2'
                      AND emailed_at IS NULL
                    RETURNING *""",
                (when, emailed_by, lead_id))
            row = cur.fetchone()
            if row is None:
                # WHY it refused, not just that it did. These are different
                # problems: "already sent" means stop, "not at L2" means the
                # lead never reached the stage where a send is owed. Reporting
                # the second as the first sent Sean looking for an email that
                # was never sent.
                cur.execute("""SELECT stage, emailed_at FROM leads
                                WHERE lead_id = %s""", (lead_id,))
                cur_row = cur.fetchone()
                if cur_row is None:
                    raise NotAtL2('no such lead')
                if cur_row['emailed_at'] is not None:
                    return None                      # genuinely already sent
                raise NotAtL2(
                    f"lead is at {cur_row['stage']}, not L2 - a send is only "
                    f"owed once a confirmed email has advanced it")
            cur.execute(
                """INSERT INTO activity (lead_id, kind, stage, summary, detail)
                   VALUES (%s, 'email_sent', 'L2', 'emailed - waiting on them', %s)""",
                (lead_id, f'by {emailed_by} at {when:%Y-%m-%d %H:%M} UTC; '
                          f'no follow-up call is scheduled - a follow-up is '
                          f'its own campaign'))
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
