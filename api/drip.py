"""
THE DRIP: a lead's email sequence.

THE SEQUENCE IS SEAN'S, NOT THE SCHEMA'S. Steps are ROWS (drip_steps), so three
steps or seven, four days or thirty, and nothing here caps the length.

⚠️  TWO DIFFERENT ANCHORS. Anyone reading one will assume the other:

      THE SCHEDULE anchors to leads.emailed_at - the FIRST send. Every step's
      delay_days is measured from there, never from the previous step, because
      chaining lets the schedule drift and anchoring does not. This is why
      emailed_at is write-once and mark_emailed() is a no-op on a second call:
      a restamp would move EVERY scheduled send and invalidate every click
      timing already recorded. Break 18 guards it.

      CLICK TIMING anchors to THE STEP'S OWN SEND. "clicked 47m after send" on
      step 3 means 47 minutes after step 3 went out. Measured from emailed_at it
      would report every later click as "11 days after send" - true of the
      sequence, useless about the email.

⚠️  THE REPLY GATE IS NOT FAIL-CLOSED, AND CANNOT BE.

    DRIP_ARCHIVE_BRIEF.md says "nothing auto-sends if detection is unavailable -
    fail closed, exactly like assert_dialable". Reply detection is MANUAL: Sean
    ticks a box and stages.record_reply() writes replied_at. There is nothing
    that can BE unavailable, so that property does not hold in the form the
    brief states.

    What is true: replied_at is a perfectly good gate for "has a reply been
    RECORDED". What is not true is that it gates "has the firm replied". The
    window between a reply arriving and Sean ticking it is real and cannot be
    closed without inbound ingest - a firm that answers on Tuesday and is ticked
    on Wednesday will receive a step that fell due on Tuesday night.

    That is accepted deliberately, at this volume, because Sean reads every
    reply. THE GUARD IS A PERSON, NOT A CODE PATH, and it is written down here
    and in HANDOFF.md rather than implied. The mitigation is that the drip never
    sends silently into the future: digest.upcoming_sends() lists tomorrow's
    sends by step and by firm, so there is a checkpoint BEFORE each batch.

    Everything else still fails closed - see autosend.eligibility().
"""

import datetime

from api import db

MAX_STEPS = 20
# Reordering parks live rows above every real position first, because
# (campaign_id, position) is UNIQUE among live steps and swapping two in place
# collides with itself. Must stay well above MAX_STEPS.
PARK_OFFSET = 10000


class BadSequence(ValueError):
    """The sequence is refused. Never saved half-valid."""


# ---------------------------------------------------------------------------
# the sequence
# ---------------------------------------------------------------------------

def steps(campaign_id):
    """Live steps, in order. Soft-deleted ones are history, not sequence."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT * FROM drip_steps
                            WHERE campaign_id = %s AND deleted_at IS NULL
                            ORDER BY position""", (campaign_id,))
            return cur.fetchall()


def step(step_id):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM drip_steps WHERE step_id = %s', (step_id,))
            return cur.fetchone()


def validate(rows) -> list:
    """
    Clean a sequence or refuse it. Returns normalised rows in order.

    REFUSES, rather than accepting and being silently wrong - the same choice
    retry_ladder.validate() makes, for the same reason:

      no steps           a drip that sends nothing while the screen says it has
                         a sequence
      duplicate delays   two steps due the same day: the lead gets two emails at
                         once and the sequence reads as one
      backwards delays   step 3 falling due before step 2, which sends the
                         sequence out of order and cannot be what anyone meant
      empty subject/body an email with no content, sent automatically
    """
    clean = []
    for i, r in enumerate(rows or [], start=1):
        subject = (r.get('subject') or '').strip()
        body = (r.get('body') or '').strip()
        raw = r.get('delay_days')
        if not subject:
            raise BadSequence(f'step {i} has no subject - it would send blank.')
        if not body:
            raise BadSequence(f'step {i} has no body - it would send blank.')
        try:
            delay = int(raw)
        except (TypeError, ValueError):
            raise BadSequence(
                f'step {i}: {raw!r} is not a number of days.')
        if delay < 0:
            raise BadSequence(f'step {i}: a delay cannot be negative.')
        if delay > 365:
            raise BadSequence(f'step {i}: {delay} days is over a year out.')
        clean.append({'position': i, 'delay_days': delay,
                      'subject': subject, 'body': body,
                      'step_id': r.get('step_id') or None})

    if not clean:
        raise BadSequence(
            'a drip needs at least one step. Saving an empty sequence would '
            'leave a campaign that looks like it sends and does not.')
    if len(clean) > MAX_STEPS:
        raise BadSequence(f'{len(clean)} steps is more than {MAX_STEPS}.')

    prev = None
    for r in clean:
        if prev is not None and r['delay_days'] == prev:
            raise BadSequence(
                f'step {r["position"]} falls due on day {r["delay_days"]}, the '
                f'same day as the step before it - the lead would get two '
                f'emails at once.')
        if prev is not None and r['delay_days'] < prev:
            raise BadSequence(
                f'step {r["position"]} falls due on day {r["delay_days"]}, '
                f'BEFORE the step before it (day {prev}). The sequence would '
                f'send out of order.')
        prev = r['delay_days']
    return clean


def save_steps(campaign_id, rows):
    """
    Replace a campaign's sequence: add, remove, reorder and edit in one act.

    ⚠️  A STEP ALREADY SENT IS NEVER RE-SENT AND NEVER RE-DATED. That rule is
    what makes editing safe with leads mid-flight, and it is enforced by
    email_sends rather than by anything here: due() will not return a step a
    lead already has a row for, whatever happens to the step's copy, its delay
    or its position.

    So the consequences fall out rather than being coded case by case:
      * editing copy      -> only leads who have not reached that step
      * changing a delay  -> reschedules only leads who have not reached it
      * deleting a step   -> SOFT deleted, so those who got it keep the record
      * inserting a step  -> leads already past that position do not go back
    """
    clean = validate(rows)
    keep = [r['step_id'] for r in clean if r['step_id']]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # Soft-delete what is gone. The send records survive on purpose.
            cur.execute(
                """UPDATE drip_steps SET deleted_at = now()
                    WHERE campaign_id = %s AND deleted_at IS NULL
                      AND NOT (step_id = ANY(%s::bigint[]))""",
                (campaign_id, keep or [0]))
            # Park every survivor out of the way first: position is UNIQUE
            # among live steps, so reordering in place collides with itself.
            # OFFSET, not negation - position has CHECK (position >= 1), and a
            # negative park violates it. The offset is above MAX_STEPS so a
            # parked row can never collide with a real one.
            cur.execute(
                """UPDATE drip_steps SET position = position + %s
                    WHERE campaign_id = %s AND deleted_at IS NULL""",
                (PARK_OFFSET, campaign_id))
            out = []
            for r in clean:
                if r['step_id']:
                    cur.execute(
                        """UPDATE drip_steps
                              SET position = %s, delay_days = %s, subject = %s,
                                  body = %s, updated_at = now()
                            WHERE step_id = %s AND campaign_id = %s
                        RETURNING *""",
                        (r['position'], r['delay_days'], r['subject'],
                         r['body'], r['step_id'], campaign_id))
                    row = cur.fetchone()
                    if row is None:
                        raise BadSequence(
                            f'step {r["step_id"]} is not on this campaign.')
                else:
                    cur.execute(
                        """INSERT INTO drip_steps
                               (campaign_id, position, delay_days, subject, body)
                           VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                        (campaign_id, r['position'], r['delay_days'],
                         r['subject'], r['body']))
                    row = cur.fetchone()
                out.append(row)
            return out


# ---------------------------------------------------------------------------
# entry: sending email 1 is the ONLY way in
# ---------------------------------------------------------------------------

def only_drip():
    """
    The single running drip, or None when there is none or several.

    AUTO-ASSIGN WHEN THERE IS EXACTLY ONE, ask only when there is a choice. No
    picker for a list of one - that is a decision the operator would make the
    same way every time.
    """
    from api import campaigns
    running = campaigns.running_drips()
    return running[0] if len(running) == 1 else None


def enter(cur, lead_id, drip_campaign_id) -> bool:
    """
    Put a lead on a drip. Takes a CURSOR: this runs inside mark_emailed()'s
    transaction, so entering the drip and stamping emailed_at commit together.
    A lead with emailed_at and no drip, or a drip and no emailed_at, is a state
    the schedule cannot be computed from.

    ⚠️  leads.campaign_id IS NOT TOUCHED. It stays the CALL campaign that
    sourced this lead, because it is the daily cap's counting key and the
    funnel's attribution key - see migration 036. The dialer needs no change:
    STAGE_DIALABLE is `AND NOT l.has_confirmed_email`, so a lead that has had
    email 1 is already not a candidate.
    """
    if not drip_campaign_id:
        return False
    cur.execute(
        """UPDATE leads SET drip_campaign_id = %s, updated_at = now()
            WHERE lead_id = %s AND drip_campaign_id IS NULL
        RETURNING lead_id""", (drip_campaign_id, lead_id))
    if cur.fetchone() is None:
        return False
    cur.execute(
        """INSERT INTO activity (lead_id, kind, summary, detail)
           VALUES (%s, 'drip', 'entered the drip',
                   'email 1 sent; the sequence is scheduled from that send')""",
        (lead_id,))
    return True


def record_send(cur, lead_id, step_id, seq, to_email, subject, sent_by):
    """
    One row per email that actually WENT, with its OWN click token.

    Returns the row. The token is per SEND, not per lead: that is what makes a
    click attributable to the step that produced it. If step 1 pulls every
    click the follow-ups are noise; if step 3 does, the opener needs rewriting.
    A per-lead count cannot tell those apart.
    """
    import secrets
    cur.execute(
        """INSERT INTO email_sends
               (lead_id, step_id, seq, to_email, subject, sent_by, click_token)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING *""",
        (lead_id, step_id, seq, to_email or '', subject, sent_by,
         secrets.token_urlsafe(16)))
    return cur.fetchone()


def sends(lead_id):
    """Every email that went to this lead, oldest first."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.*, st.position
                             FROM email_sends s
                             LEFT JOIN drip_steps st ON st.step_id = s.step_id
                            WHERE s.lead_id = %s
                            ORDER BY s.seq, s.sent_at""", (lead_id,))
            return cur.fetchall()


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
#
# Every stop is a named fragment so the break pass can remove exactly one and
# watch exactly one test go red - the same shape as dialer.SELECT_DUE.
#
# REPLIED is first because it is the one that matters most: a reply stops the
# sequence dead. A CLICK DOES NOT - a click is interest, not an answer, and
# stopping on one would silence the sequence exactly when it is working.
# ---------------------------------------------------------------------------

REPLIED_STOP = 'AND l.replied_at IS NULL'
ARCHIVED_STOP = "AND l.status <> 'archived'"
# A drip only sends while ITS campaign is running. is_running is the switch,
# exactly as it is for a call campaign, and one_running_campaign is scoped to
# type='call' so many drips run at once.
RUNNING_STOP = "AND c.is_running AND c.type = 'drip'"
# Terminal states a person or the system has already reached.
TERMINAL_STOP = ("AND l.status NOT IN ('dnc','bad_email','won','lost',"
                 "'demo_booked','unsubscribed')")
# The address, not the lead. Outlives everything.
DO_NOT_SEND_STOP = ('AND NOT EXISTS (SELECT 1 FROM email_do_not_send d '
                    'WHERE d.email = lower(btrim(l.dm_email)))')
# A STEP ALREADY SENT IS NEVER RE-SENT. This is the fragment that makes editing
# a live sequence safe: it is a FACT in email_sends, not a count.
ALREADY_SENT_STOP = ('AND NOT EXISTS (SELECT 1 FROM email_sends es '
                     'WHERE es.lead_id = l.lead_id AND es.step_id = s.step_id)')
# THE SCHEDULE. delay_days from emailed_at, never from the previous step.
DUE_NOW = ("AND l.emailed_at + (s.delay_days || ' days')::interval <= now()")

SELECT_DUE = """
    SELECT l.lead_id, l.company, l.dm_email, l.dm_name, l.emailed_at,
           s.step_id, s.position, s.delay_days, s.subject, s.body,
           c.campaign_id AS drip_campaign_id, c.name AS drip_name,
           (l.emailed_at + (s.delay_days || ' days')::interval) AS due_at
      FROM leads l
      JOIN campaign_configs c ON c.campaign_id = l.drip_campaign_id
      JOIN drip_steps s       ON s.campaign_id = c.campaign_id
                             AND s.deleted_at IS NULL
     WHERE l.emailed_at IS NOT NULL
       {running}
       {replied}
       {archived}
       {terminal}
       {do_not_send}
       {already_sent}
       {due_now}
     -- The EARLIEST unsent due step for each lead, so a sequence cannot skip
     -- ahead if two fall due together after a pause.
     ORDER BY l.lead_id, s.position
"""


def _build_select(due_clause=DUE_NOW):
    return SELECT_DUE.format(
        running=RUNNING_STOP, replied=REPLIED_STOP, archived=ARCHIVED_STOP,
        terminal=TERMINAL_STOP, do_not_send=DO_NOT_SEND_STOP,
        already_sent=ALREADY_SENT_STOP, due_now=due_clause)


def due(limit: int = 50):
    """
    (lead, step) pairs whose delay has elapsed - ONE per lead, the earliest
    unsent step.

    Selection is deliberately loose; every exclusion is re-checked inside the
    transaction that sends, the same lesson as dial_one and sender.send_one.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_build_select())
            seen, out = set(), []
            for r in cur.fetchall():
                if r['lead_id'] in seen:
                    continue
                seen.add(r['lead_id'])
                out.append(r)
                if len(out) >= limit:
                    break
            return out


def upcoming(within_hours: int = 24, limit: int = 200):
    """
    What goes out in the next `within_hours`, by step and by FIRM.

    ⚠️ THIS IS THE MITIGATION FOR A GATE THAT CANNOT FAIL CLOSED. Reply
    detection is manual, so nothing in code can know a firm has answered until
    Sean ticks it. The drip therefore never sends silently into the future: the
    digest lists tomorrow's sends by name so there is a checkpoint BEFORE each
    batch, and one lead can be stopped individually rather than pausing the
    whole drip.
    """
    clause = ("AND l.emailed_at + (s.delay_days || ' days')::interval"
              " <= now() + (%(h)s || ' hours')::interval")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_build_select(clause), {'h': within_hours})
            seen, out = set(), []
            for r in cur.fetchall():
                if r['lead_id'] in seen:
                    continue
                seen.add(r['lead_id'])
                out.append(r)
                if len(out) >= limit:
                    break
            out.sort(key=lambda r: (r['due_at'], r['company'] or ''))
            return out


# ---------------------------------------------------------------------------
# sending a step
# ---------------------------------------------------------------------------

def send_step(cfg, row) -> dict:
    """
    {'sent': bool, 'detail': str}. NEVER RAISES.

    Same shape as sender.send_one, and for the same reasons: RE-CHECK
    everything inside the transaction that sends, refuse loudly, audit either
    way, and send exactly once.

    SENDS EXACTLY ONCE. The email_sends row is written BEFORE the API call and
    (lead_id, step_id) is UNIQUE, so a crash after the insert loses one email
    and a crash before it sends nothing. A second send to a real person is the
    failure that must be impossible; a lost one costs Sean thirty seconds.
    """
    from api import archive, autosend, campaigns, drafts, guards, mail, stages
    from api.guards import EmailRefused

    lead_id, step_id = row['lead_id'], row['step_id']
    to_email = (row.get('dm_email') or '').strip()
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lead_id,))
                lead = cur.fetchone()
                if lead is None:
                    return {'sent': False, 'detail': 'no such lead'}
                lead = dict(lead)
                camp = campaigns.get(lead.get('drip_campaign_id'))

                # 1. THE GATE. The same seven exclusions email 1 uses, reused
                #    verbatim - the brief requires them on EVERY step, not just
                #    the first. `step=True` swaps the email-1 mode check for the
                #    drip's own switch; nothing else differs.
                decision = autosend.eligibility(lead, camp, drip_step=True)
                if not decision['ok']:
                    reason = '; '.join(decision['reasons'])
                    _audit(cur, lead_id, to_email, 'refused_ineligible', reason)
                    return {'sent': False, 'detail': reason}

                if archive.is_do_not_send(to_email):
                    _audit(cur, lead_id, to_email, 'refused_do_not_send',
                           f'{to_email} is on the email do-not-send list')
                    return {'sent': False, 'detail': 'do-not-send'}

                # 2. THE DEV GUARD. Last line, and it fails closed.
                try:
                    guards.assert_emailable(to_email, cfg)
                except EmailRefused as exc:
                    _audit(cur, lead_id, to_email, 'refused_allowlist', str(exc))
                    return {'sent': False, 'detail': str(exc)}

        # 3. CLAIM THE SEND BEFORE MAKING IT.
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM email_sends '
                            'WHERE lead_id = %s', (lead_id,))
                seq = cur.fetchone()['n'] + 1
                try:
                    send = record_send(cur, lead_id, step_id, seq, to_email,
                                       row['subject'],
                                       f'auto:drip:{cfg.SENDER_DOMAIN}')
                except Exception:
                    # The UNIQUE (lead_id, step_id) index refused it: this step
                    # has already gone to this lead. Not an error - a race with
                    # another tick, and the correct outcome is to do nothing.
                    return {'sent': False, 'detail': 'step already sent'}

        # 4. Render with the SEND's own token, so the click attributes to this
        #    step rather than to the lead.
        body = drafts.render(row['body'], drafts.values_for(
            lead, camp, send=send))
        subject = drafts.render(row['subject'], drafts.values_for(
            lead, camp, send=send))
        result = mail.send(cfg, to_email, subject, body,
                           sender_email=(camp or {}).get('sender_email'),
                           sender_name=(camp or {}).get('sender_name'))

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if result.get('ok'):
                    _audit(cur, lead_id, to_email, 'sent_drip',
                           f'step {row["position"]} as {(camp or {}).get("sender_email")}')
                    cur.execute(
                        """INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'drip',%s,%s)""",
                        (lead_id, f'drip step {row["position"]} sent',
                         f'{subject[:200]}'))
                else:
                    # The email_sends row STAYS. We do not know whether Brevo
                    # accepted it, and deleting it would let the next tick send
                    # the same step again.
                    _audit(cur, lead_id, to_email, 'send_failed',
                           str(result.get('detail'))[:400])
                    cur.execute(
                        """INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'note','drip send FAILED - needs a person',%s)""",
                        (lead_id, str(result.get('detail'))[:400]))
        if result.get('ok'):
            _maybe_finish(cfg, lead_id)
        return {'sent': bool(result.get('ok')),
                'detail': str(result.get('detail'))[:200]}

    except Exception as exc:
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    _audit(cur, lead_id, to_email, 'refused_error',
                           f'{type(exc).__name__}: {exc}')
        except Exception:
            pass
        return {'sent': False, 'detail': f'{type(exc).__name__}: {exc}'}


def _audit(cur, lead_id, to_email, outcome, detail=''):
    cur.execute(
        """INSERT INTO email_audit (lead_id, to_email, outcome, detail)
           VALUES (%s,%s,%s,%s)""", (lead_id, to_email, outcome, detail[:500]))


def _maybe_finish(cfg, lead_id) -> bool:
    """
    THE SEQUENCE TERMINATES. After the last step, silence means archive
    (no_reply) - or hold, if the campaign says so.

    "Everything terminates" is the property the whole model rests on: a lead is
    worked by the machine, worked by Sean, or resting. A drip that runs out of
    steps and leaves the lead in place is a fourth state - limbo - which is
    exactly what the brief refuses.
    """
    from api import archive, campaigns
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT l.drip_campaign_id,
                                  (SELECT count(*) FROM drip_steps s
                                    WHERE s.campaign_id = l.drip_campaign_id
                                      AND s.deleted_at IS NULL)  AS total,
                                  (SELECT count(*) FROM email_sends es
                                    JOIN drip_steps s2 ON s2.step_id = es.step_id
                                   WHERE es.lead_id = l.lead_id
                                     AND s2.campaign_id = l.drip_campaign_id
                                     AND s2.deleted_at IS NULL) AS done
                             FROM leads l WHERE l.lead_id = %s""", (lead_id,))
            r = cur.fetchone()
    if not r or not r['drip_campaign_id'] or r['done'] < r['total']:
        return False
    camp = campaigns.get(r['drip_campaign_id']) or {}
    if camp.get('after_last_step') == 'hold':
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s,'drip','drip finished - HELD for a person',
                               'last step sent, no reply. The campaign is set '
                               'to hold rather than archive.')""", (lead_id,))
        return True
    archive.archive(lead_id, 'no_reply', by='drip')
    return True


def run_once(cfg, limit: int = 50) -> dict:
    sent = refused = 0
    for row in due(limit):
        r = send_step(cfg, row)
        sent += bool(r['sent'])
        refused += not r['sent']
    return {'sent': sent, 'refused': refused}


# ---------------------------------------------------------------------------
# stopping
# ---------------------------------------------------------------------------

STOP_REASONS = ('replied', 'bounced', 'unsubscribed', 'demo_booked',
                'by_hand', 'no_reply')


def stop(lead_id, reason: str, by: str = 'operator') -> bool:
    """
    Take a lead off the drip. EVERY STOP RECORDS WHY.

    A stop with no reason is a lead that silently went quiet, and six months on
    nothing can say whether it said no, bounced, or was simply forgotten - the
    same argument as archive_reason.

    An unsubscribe suppresses EMAIL ONLY. Someone who does not want our emails
    has not given up the right to be phoned about a case they asked about, so
    this NEVER writes to `suppression`. A prose "take me off your list" in a
    reply is broader, and that is a person's call at the DNC button.
    """
    if reason not in STOP_REASONS:
        raise ValueError(f'{reason!r} is not a stop reason. '
                         f'One of: {", ".join(STOP_REASONS)}')
    from api import archive
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT drip_campaign_id, dm_email FROM leads
                            WHERE lead_id = %s FOR UPDATE""", (lead_id,))
            row = cur.fetchone()
            if row is None:
                return False
            cur.execute(
                """UPDATE leads SET drip_campaign_id = NULL, updated_at = now()
                    WHERE lead_id = %s""", (lead_id,))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s,'drip',%s,%s)""",
                (lead_id, f'drip STOPPED ({reason})', f'by {by}'))

    if reason == 'unsubscribed' and row['dm_email']:
        archive.do_not_send(row['dm_email'], 'unsubscribed', f'drip:{by}')
        archive.archive(lead_id, 'unsubscribed', by=by)
    elif reason == 'bounced' and row['dm_email']:
        archive.do_not_send(row['dm_email'], 'bad_email', f'drip:{by}')
        # AND SET THE STATUS, so the bounce is VISIBLE.
        #
        # The address going on the do-not-send list is what stops us mailing it
        # again; it is not what tells anyone it happened. Without this the lead
        # sits at 'emailed' with no drip and no explanation - in no filter, on
        # no queue, simply stopped. A LEAD FAILING SILENTLY is the shape every
        # other guard in this system exists to prevent.
        #
        # NOT archived, deliberately: the brief says bounced -> bad_email, back
        # to Sean. A bounce is a bad ADDRESS, not a bad firm, and it usually
        # wants a corrected one - which is a person's job and needs the lead in
        # front of them rather than resting for six months.
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE leads SET status = 'bad_email',
                              updated_at = now()
                        WHERE lead_id = %s""", (lead_id,))
                # The detail is BOUND, not inlined: a multi-line SQL string
                # literal would put its own newlines and indentation into the
                # timeline text.
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s, 'drip', 'status -> bad_email (bounced)', %s)""",
                    (lead_id,
                     'the ADDRESS is on the do-not-send list; the lead is left '
                     'for a person because a bounce usually wants a corrected '
                     'address, not an archive'))
    return True
