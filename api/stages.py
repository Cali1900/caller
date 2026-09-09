"""
The one transition: no confirmed email -> a confirmed email. It stops there.

  not confirmed   cold call the front desk. Goal: a name and a confirmed email.
  confirmed       we have the email. Nothing is dialed - the lead waits.

⚠️ `leads.stage` IS GONE (migration 035). It held two values and therefore one
bit, was named after a four-rung ladder that no longer exists, and BECAUSE
"stage" does not sound like state an archive return should clear,
archive.return_due() did not clear it - every lead archived after a send came
back permanently undialable. The column is now `leads.has_confirmed_email`,
a boolean, and the dialer filter reads "AND NOT l.has_confirmed_email".

THE 'L1'/'L2' VOCABULARY SURVIVES WHERE IT IS STILL TRUE: `calls.stage` and
`scores.stage` are HISTORY on past records, and retell.agent_for() selects an
agent by that name. Those are not the lead's current state, so they keep the
label - see stage_label() below, which is the ONE place the boolean is
translated back.

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

# L1 / L2 as a LABEL, for the places that legitimately still speak it: the
# agent Retell should run (retell.agent_for), and the stage recorded on a call
# or a score, which is history about that record and not the lead's state now.
#
# ONE translation, in one place. Scattering `'L2' if x else 'L1'` through the
# app is how the two vocabularies drift and how somebody eventually writes a
# third.
L1, L2 = 'L1', 'L2'


def stage_label(lead_or_flag) -> str:
    """'L2' if a confirmed email has been captured, else 'L1'."""
    if isinstance(lead_or_flag, dict):
        flag = lead_or_flag.get('has_confirmed_email')
    else:
        flag = lead_or_flag
    return L2 if flag else L1


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
              SET has_confirmed_email = true, email_confirmed_at = now(),
                  updated_at = now()
            WHERE lead_id = %s
              AND NOT has_confirmed_email
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
                    WHERE lead_id = %s AND has_confirmed_email
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
                cur.execute("""SELECT has_confirmed_email, emailed_at
                                 FROM leads WHERE lead_id = %s""", (lead_id,))
                cur_row = cur.fetchone()
                if cur_row is None:
                    raise NotAtL2('no such lead')
                if cur_row['emailed_at'] is not None:
                    return None                      # genuinely already sent
                raise NotAtL2(
                    f"lead is at {stage_label(cur_row)}, not L2 - a send is "
                    f"only owed once a confirmed email has advanced it")
            # STAMP THE SEND, and enter the drip, in THIS transaction.
            #
            # email 1's email_sends row already exists (prepared when the draft
            # was rendered - see clicks.token_for), so this marks it sent rather
            # than creating it. If no draft was ever generated there is nothing
            # to stamp and one is created now, so a hand-sent email is still a
            # recorded send with a token.
            from api import drip as _drip
            cur.execute("""UPDATE email_sends SET sent_at = %s, sent_by = %s
                            WHERE lead_id = %s AND seq = 1 AND sent_at IS NULL
                        RETURNING send_id""", (when, emailed_by, lead_id))
            if cur.fetchone() is None:
                cur.execute("""SELECT 1 FROM email_sends
                                WHERE lead_id = %s AND seq = 1""", (lead_id,))
                if cur.fetchone() is None:
                    _drip.record_send(cur, lead_id, None, 1,
                                      row.get('dm_email') or '', None,
                                      emailed_by)
                    cur.execute("""UPDATE email_sends SET sent_at = %s
                                    WHERE lead_id = %s AND seq = 1""",
                                (when, lead_id))

            # ENTER THE DRIP. Sending email 1 is the ONLY way in, and it commits
            # with the emailed_at stamp: a lead with emailed_at and no drip, or
            # a drip and no emailed_at, is a state the schedule cannot be
            # computed from. Auto-assigned when exactly ONE drip is running -
            # no picker for a list of one. With several, a person chooses on the
            # lead, and drip_campaign_id stays NULL until they do.
            #
            # leads.campaign_id IS NOT TOUCHED - it stays the CALL campaign that
            # sourced the lead, because it is the daily cap's counting key and
            # the funnel's attribution key. See migration 036.
            only = _drip.only_drip()
            if only:
                _drip.enter(cur, lead_id, only['campaign_id'])

            # The pipeline stage moves with the send, so the forecast reads
            # a real status instead of deriving one.
            from api import pipeline
            pipeline.advance(cur, lead_id, 'emailed', f'email 1 sent by {emailed_by}')
            cur.execute(
                """INSERT INTO activity (lead_id, kind, stage, summary, detail)
                   VALUES (%s, 'email_sent', 'L2', 'emailed - waiting on them', %s)""",
                (lead_id, f'by {emailed_by} at {when:%Y-%m-%d %H:%M} UTC; '
                          f'no follow-up call is scheduled - a follow-up is '
                          f'its own campaign'))
            return row


def record_reply(lead_id, when=None, note: str = '', by: str = 'operator'):
    """
    THEY REPLIED. Stop calling, stop sending.

    Today's caller is a person ticking "I got a reply" on the lead - Sean reads
    every reply at this volume, and a human who has read it is a better
    detector than anything automatic. When ingest is eventually built it calls
    THIS, as a second writer to the same field: the auto-send gate, the
    dialer's guard and the drip all keep reading one fact from one place.

    WRITE-ONCE, like emailed_at. A second call is a no-op rather than a
    restamp: the FIRST reply is when they answered, and moving that timestamp
    would rewrite the history every "N days after" measurement rests on. Undo
    is a separate, explicit, audited operation - see clear_reply().
    """
    when = when or datetime.datetime.now(datetime.UTC)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads SET replied_at = %s, status = 'engaged',
                          reply_note = NULLIF(%s,''), replied_by = %s,
                          updated_at = now()
                    WHERE lead_id = %s AND replied_at IS NULL
                    RETURNING lead_id""",
                (when, (note or '').strip(), by, lead_id))
            if cur.fetchone() is None:
                return False
            detail = f'recorded by {by}'
            if (note or '').strip():
                detail += f' \u2014 "{note.strip()[:400]}"'
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'reply', 'THEY REPLIED - recorded BY HAND', %s)""",
                (lead_id, detail))
            return True


def clear_reply(lead_id, by: str = 'operator') -> bool:
    """
    Untick it. A misclick must be undoable.

    Deliberately NOT a silent revert: it writes to the timeline exactly as the
    tick did, so the record shows a reply was recorded and then WITHDRAWN
    rather than showing nothing at all. A lead whose reply quietly vanished is
    one somebody emails again without knowing why they should not.

    Status is NOT moved back automatically. `engaged` may have been reached by
    a click as well, and guessing which is wrong more often than leaving it -
    the status dropdown is right there.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT replied_at FROM leads WHERE lead_id = %s',
                        (lead_id,))
            row = cur.fetchone()
            if row is None or row['replied_at'] is None:
                return False
            cur.execute(
                """UPDATE leads SET replied_at = NULL, reply_note = NULL,
                          replied_by = NULL, updated_at = now()
                    WHERE lead_id = %s""", (lead_id,))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'reply', 'reply record WITHDRAWN by hand', %s)""",
                (lead_id,
                 f'was recorded {row["replied_at"]:%Y-%m-%d %H:%M} UTC; '
                 f'withdrawn by {by}. Status left as it is - a click can also '
                 f'have made this lead engaged.'))
            return True
