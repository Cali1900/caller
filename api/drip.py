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


def _flag(v, default: bool = True) -> bool:
    """
    ⚠️ AN ABSENT FIELD IS NOT A FIELD SET TO FALSE.

    An unchecked HTML checkbox posts NOTHING, so from a form 'absent' does mean
    off - and the web handler therefore ALWAYS supplies this key explicitly. But
    a programmatic caller (a test, a seed, a script) passes rows without it and
    means 'a normal enabled step'. Treating those two the same way silently
    disabled every step created outside the form.

    Same fault the retry ladder hit: an absent ladder field is not a ladder set
    to empty, and collapsing them made every older form post reject a save.
    """
    if v is None:
        return default
    return str(v).strip().lower() in ('1', 'true', 'on', 'yes')


def _minutes(raw, i) -> int:
    """
    Step 1's delay, in minutes from entering the drip. 0 = immediately.

    Blank reads as 0 rather than raising: 'immediately' is the sensible reading
    of an empty timing box on the FIRST email, and it is what position=1 meant
    before this field existed.
    """
    v = ('' if raw is None else str(raw)).strip()
    if not v:
        return 0
    try:
        m = int(v)
    except ValueError:
        raise BadSequence(f'step {i}: {raw!r} is not a number of minutes.')
    if m < 0:
        raise BadSequence(f'step {i}: a delay cannot be negative.')
    if m > 10080:
        raise BadSequence(
            f'step {i}: {m} minutes is over a week. Use a day-based step '
            f'instead - this control is for the first send.')
    return m


class BadSequence(ValueError):
    """The sequence is refused. Never saved half-valid."""


# ---------------------------------------------------------------------------
# the sequence
# ---------------------------------------------------------------------------

def steps(campaign_id, enabled_only: bool = False):
    """
    Steps in order. Soft-deleted ones are history, not sequence.

    DISABLED STEPS ARE INCLUDED BY DEFAULT, because the EDITOR has to show them
    - a step you cannot see is a step you cannot turn back on. due() applies
    ENABLED_STOP in SQL rather than calling this, so the two cannot disagree
    about what is live.
    """
    extra = ' AND enabled' if enabled_only else ''
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT * FROM drip_steps
                             WHERE campaign_id = %s AND deleted_at IS NULL{extra}
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
        if not subject:
            raise BadSequence(f'step {i} has no subject - it would send blank.')
        if not body:
            raise BadSequence(f'step {i} has no body - it would send blank.')

        # ⚠️ STEP 1 IS TIMED IN MINUTES FROM ENTERING THE DRIP; STEPS 2+ IN
        # DAYS FROM emailed_at. Step 1 IS the first send, so it cannot be N
        # days from itself - a day field on it read as a control and was not
        # one. delay_days is still carried on step 1 (as 0) so the ordering
        # check below has one scale to compare on.
        if i == 1:
            mins = _minutes(r.get('delay_minutes'), i)
            clean.append({'position': i, 'delay_days': 0,
                          'delay_minutes': mins, 'subject': subject,
                          'body': body, 'enabled': _flag(r.get('enabled')),
                          'step_id': r.get('step_id') or None})
            continue

        raw = r.get('delay_days')
        try:
            delay = int(raw)
        except (TypeError, ValueError):
            raise BadSequence(
                f'step {i}: {raw!r} is not a number of days.')
        if delay < 1:
            raise BadSequence(
                f'step {i}: it must be at least a day after the first send - '
                f'day 0 is step 1, and two emails in one minute reads as a '
                f'malfunction.')
        if delay > 365:
            raise BadSequence(f'step {i}: {delay} days is over a year out.')
        clean.append({'position': i, 'delay_days': delay,
                      'delay_minutes': None, 'subject': subject,
                      'body': body, 'enabled': _flag(r.get('enabled')),
                      'step_id': r.get('step_id') or None})

    if not clean:
        raise BadSequence(
            'a drip needs at least one step. Saving an empty sequence would '
            'leave a campaign that looks like it sends and does not.')
    if len(clean) > MAX_STEPS:
        raise BadSequence(f'{len(clean)} steps is more than {MAX_STEPS}.')

    # ⚠️ ORDERING IS CHECKED ACROSS DISABLED STEPS TOO. A disabled step keeps
    # its delay, so skipping it here would let a sequence be saved that becomes
    # BACKWARDS the moment somebody re-enables it - and the re-enable is a
    # single checkbox with no validation of its own.
    #
    # Step 1 is excluded because it is not on this scale at all: its timing is
    # minutes from entering the drip, and it always precedes every day-based
    # step by construction.
    prev = None
    for r in clean[1:]:
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
                              SET position = %s, delay_days = %s,
                                  delay_minutes = %s, subject = %s,
                                  body = %s, enabled = %s, updated_at = now()
                            WHERE step_id = %s AND campaign_id = %s
                        RETURNING *""",
                        (r['position'], r['delay_days'], r['delay_minutes'],
                         r['subject'], r['body'], r['enabled'],
                         r['step_id'], campaign_id))
                    row = cur.fetchone()
                    if row is None:
                        raise BadSequence(
                            f'step {r["step_id"]} is not on this campaign.')
                else:
                    cur.execute(
                        """INSERT INTO drip_steps
                               (campaign_id, position, delay_days,
                                delay_minutes, subject, body, enabled)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (campaign_id, r['position'], r['delay_days'],
                         r['delay_minutes'], r['subject'], r['body'],
                         r['enabled']))
                    row = cur.fetchone()
                out.append(row)
            return out


# ---------------------------------------------------------------------------
# entry: sending email 1 is the ONLY way in
# ---------------------------------------------------------------------------

def drip_for(campaign):
    """
    WHICH DRIP A CALL CAMPAIGN'S LEADS ENTER when email 1 is sent.

    THE CAMPAIGN DECIDES (default_drip_id). A campaign already owns email 1's
    copy, so owning what FOLLOWS email 1 is the same shape rather than a new
    concept - and it is the only thing that survives a second drip existing.

    ⚠️ only_drip() USED TO BE THE MECHANISM AND IT DOES NOT SCALE PAST ONE. It
    assigns only when exactly ONE drip runs, so the moment there are two - a
    call-sourced sequence and an imported one, for any reason at all - every
    call-sourced lead that got email 1 joined NO DRIP: email 1 out, lead at
    'emailed', nothing following up, and nothing saying so until somebody opened
    that lead. It is a FALLBACK now, for the single-drip case where naming one
    would be ceremony.

    Returns a campaign row, or None. A None here is not silent: the lead shows
    up in /today's needs-you queue as emailed and on no drip.
    """
    from api import campaigns
    want = (campaign or {}).get('default_drip_id')
    if want:
        named = campaigns.get(want)
        # Only if it is still a RUNNING drip. A campaign pointing at a stopped
        # or deleted sequence must not silently fall back to whatever else is
        # running - that is how a lead lands on the wrong copy.
        if named and named['type'] == 'drip' and named['is_running']:
            return named
        return None
    return only_drip()


def only_drip():
    """
    The single running drip, or None when there is none or several.

    A FALLBACK, not the mechanism - see drip_for(). Auto-assigning from a list
    of one is a decision nobody would make differently; auto-assigning from a
    list of two is a guess.
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
    # STAMP WHEN IT ENTERED. Step 1's delay is measured from here, so without
    # this a 15-minute step 1 would have nothing to count from.
    cur.execute(
        """UPDATE leads SET drip_campaign_id = %s,
                          drip_entered_at = now(), updated_at = now()
            WHERE lead_id = %s AND drip_campaign_id IS NULL
        RETURNING lead_id""", (drip_campaign_id, lead_id))
    if cur.fetchone() is None:
        return False
    # ⚠️ STEP 1 IS THE FIRST EMAIL, WHOEVER SENT IT.
    #
    # A call-sourced lead has already had email 1 - that send is what let it in
    # here - and its email_sends row carries step_id NULL because no drip existed
    # when it went. ALREADY_SENT_STOP matches on step_id, so without this the
    # drip's step 1 (delay 0) would be unsent and due IMMEDIATELY: the firm gets
    # the same opener twice, minutes apart.
    #
    # Linking email 1 to step 1 makes both entry paths agree:
    #
    #   call-sourced   email 1 IS step 1 -> next due is step 2, at its delay
    #   imported       no email 1 -> step 1 is due now, and sending it stamps
    #                  emailed_at (see DUE_NOW and send_step)
    cur.execute(
        """UPDATE email_sends es SET step_id = (
                   SELECT s.step_id FROM drip_steps s
                    WHERE s.campaign_id = %s AND s.deleted_at IS NULL
                    ORDER BY s.position LIMIT 1)
            WHERE es.lead_id = %s AND es.seq = 1 AND es.step_id IS NULL
              AND es.sent_at IS NOT NULL""",
        (drip_campaign_id, lead_id))
    linked = cur.rowcount
    cur.execute(
        """INSERT INTO activity (lead_id, kind, summary, detail)
           VALUES (%s, 'drip', 'entered the drip', %s)""",
        (lead_id,
         'email 1 counts as step 1; the sequence is scheduled from that send'
         if linked else
         'no email has gone yet - step 1 is due now and will start the clock'))
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

# A DISABLED STEP IS SKIPPED, and it keeps its copy and its send records.
# Distinct from deleted_at: turning a step off is 'try the sequence without
# step 3', deleting it is 'that step is gone'. Without this the only way to
# test a sequence without one step was to delete it and retype the copy.
ENABLED_STOP = 'AND s.enabled'

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
#
# ⚠️ AN IMPORTED LEAD HAS NO emailed_at YET, and STEP 1 IS WHAT CREATES IT.
#
# A call-sourced lead reaches a drip by having email 1 sent, so emailed_at is
# already stamped and every step measures from it. An email-only lead is added
# to a drip DIRECTLY - there is no email 1 before the sequence, because step 1
# IS email 1.
#
# So a lead on a drip with no emailed_at has step 1 due immediately, and sending
# it stamps emailed_at through the same write-once path (stages.mark_emailed).
# From that instant the lead is indistinguishable from a call-sourced one and
# every later step anchors normally.
#
# ONE ANCHOR, not two. The alternative was a separate sequence_started_at
# column, which would mean two columns that must agree forever; emailed_at
# already means "when the sequence started" and keeps meaning exactly that.
# STEP 1's clock runs from drip_entered_at, NOT from emailed_at - emailed_at
# does not exist yet, because step 1 is what creates it. Steps 2+ run from
# emailed_at. Different events, so different anchors; see migration 040 for why
# that is not the duplicate-anchor fault rejected when the import was designed.
#
# coalesce on drip_entered_at so a lead that predates the column still works:
# an absent entry time reads as "already elapsed", never as "never due", which
# is the direction that fails loudly rather than silently.
DUE_NOW = ("AND ((l.emailed_at IS NOT NULL"
           "      AND l.emailed_at + (s.delay_days || ' days')::interval"
           "          <= now())"
           "  OR (l.emailed_at IS NULL AND s.position = 1"
           "      AND coalesce(l.drip_entered_at, 'epoch'::timestamptz)"
           "          + (coalesce(s.delay_minutes, 0) || ' minutes')::interval"
           "          <= now()))")

SELECT_DUE = """
    SELECT l.lead_id, l.company, l.dm_email, l.dm_name, l.emailed_at,
           s.step_id, s.position, s.delay_days, s.subject, s.body,
           c.campaign_id AS drip_campaign_id, c.name AS drip_name,
           (l.emailed_at + (s.delay_days || ' days')::interval) AS due_at
      FROM leads l
      JOIN campaign_configs c ON c.campaign_id = l.drip_campaign_id
      JOIN drip_steps s       ON s.campaign_id = c.campaign_id
                             AND s.deleted_at IS NULL
     -- NOT "emailed_at IS NOT NULL": an imported lead is on a drip before any
     -- email has gone out, and step 1 is the one that stamps it. See DUE_NOW.
     WHERE true
       {running}
       {replied}
       {archived}
       {terminal}
       {do_not_send}
       {enabled}
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
        enabled=ENABLED_STOP,
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

        # STEP 1 OF AN IMPORTED LEAD *IS* EMAIL 1, so it starts the clock.
        # Stamped through stages.mark_emailed - the SAME write-once transition
        # the button and the sender use, not a second implementation of it - so
        # every later step anchors to it exactly as a call-sourced lead's does.
        if result.get('ok') and lead.get('emailed_at') is None:
            try:
                stages.mark_emailed(lead_id,
                                    emailed_by=f'auto:drip:{cfg.SENDER_DOMAIN}')
            except stages.NotAtL2 as exc:
                # The mail HAS gone. Losing the stamp would leave the sequence
                # with no anchor and every later step permanently undue, so this
                # is loud rather than swallowed.
                print(f'[drip] SENT step 1 to {lead_id} but could not stamp '
                      f'emailed_at: {exc}', flush=True)
                with db.get_conn() as conn2:
                    with conn2.cursor() as cur2:
                        _audit(cur2, lead_id, to_email, 'stamp_failed', str(exc))

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
                                  -- ENABLED ONLY. A disabled tail step would
                                  -- otherwise never be 'done', so the sequence
                                  -- would never terminate and the lead would
                                  -- sit in the drip forever - the limbo the
                                  -- whole model refuses.
                                  (SELECT count(*) FROM drip_steps s
                                    WHERE s.campaign_id = l.drip_campaign_id
                                      AND s.deleted_at IS NULL
                                      AND s.enabled)             AS total,
                                  (SELECT count(*) FROM email_sends es
                                    JOIN drip_steps s2 ON s2.step_id = es.step_id
                                   WHERE es.lead_id = l.lead_id
                                     AND s2.campaign_id = l.drip_campaign_id
                                     AND s2.deleted_at IS NULL
                                     AND s2.enabled)            AS done
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
            # drip_entered_at goes with it: if this lead is ever put on a drip
            # again, step 1 must be timed from THAT entry, not the old one.
            cur.execute(
                """UPDATE leads SET drip_campaign_id = NULL,
                          drip_entered_at = NULL, updated_at = now()
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
