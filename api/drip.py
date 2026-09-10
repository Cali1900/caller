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


# Step 1's ceiling, in days. Seven, because drip_steps_delay_minutes_sane caps
# delay_minutes at 10080 - the constraint is the authority and this is the same
# number in the operator's unit. Raising one without the other is a 500.
MAX_STEP1_DAYS = 7


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
    # ⚠️ THE CEILING IS THE DATABASE'S, EXPRESSED IN THE UNIT THE OPERATOR TYPES.
    # drip_steps_delay_minutes_sane is CHECK (delay_minutes <= 10080) - seven
    # days - and that constraint is the authority; this is its message.
    #
    # It used to report the excess in MINUTES, left over from when step 1's
    # timing was a number plus a minutes/hours unit. The screen offers DAYS, so
    # typing 30 - inside the range the input allowed - was refused with "43200
    # minutes is over a week": a number nobody typed, in a unit nobody chose.
    # THE INPUT NOW STOPS AT 7 TOO. A control must not offer a value the save
    # refuses, and it must not offer one the DATABASE refuses either - my first
    # pass at this raised the ceiling here alone and turned the refusal into a
    # 500 from Postgres, which is the same fault one layer down.
    if m > MAX_STEP1_DAYS * 1440:
        raise BadSequence(
            f'step {i}: {m // 1440} days is more than {MAX_STEP1_DAYS} after '
            f'joining the drip. If the first email is that far out, import the '
            f'list when you want it to go rather than parking it here.')
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
        # ⚠️ BLANK IS ITS OWN FAULT, NAMED AS ITSELF. A missing delay used to be
        # reported as "'' is not a number of days", which is true and useless:
        # the operator did not type a bad number, they left a box empty, and the
        # message has to say which box on which step so the fix is obvious
        # without hunting. Same class as a missing subject or body - REFUSED,
        # never defaulted, because guessing a delay silently reschedules an
        # email somebody else has to explain.
        if raw is None or str(raw).strip() == '':
            raise BadSequence(
                f'step {i} has no delay - say how many days after the first '
                f'send it goes out. Nothing was saved.')
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

def link_email_1(cur, lead_id, step_id) -> bool:
    """
    Attach email 1's send row to step 1, if there is one to attach. True when it
    linked - and a link means step 1 must NOT be sent.

    ⚠️ THIS IS THE HALF OF enter() THAT COULD NOT DIE. Assignment is gone, but
    the link is what stops a CALL-SOURCED lead receiving the cold opener twice:
    email 1 already went to that firm, its email_sends row carries step_id NULL
    because no drip existed when it went, and ALREADY_SENT_STOP matches on
    step_id. Without the link, step 1 is unsent with delay_days 0 and
    `emailed_at + 0 days <= now()` is true, so a firm we CALLED receives an
    opener that says nothing about the call.

    It used to happen once, at assignment. Membership is derived now, so there is
    no assignment to hang it on - it happens LAZILY, at selection, which is the
    same rule triggered by a different event. The two entry paths still agree:

      call-sourced   email 1 IS step 1 -> linked here, next due is step 2
      imported       no email 1 -> nothing to link, step 1 sends normally
    """
    cur.execute(
        """UPDATE email_sends SET step_id = %s
            WHERE lead_id = %s AND seq = 1 AND step_id IS NULL
              AND sent_at IS NOT NULL""", (step_id, lead_id))
    if not cur.rowcount:
        return False
    cur.execute(
        """INSERT INTO activity (lead_id, kind, summary, detail)
           VALUES (%s,'drip','email 1 counts as step 1',%s)""",
        (lead_id, 'linked when the drip first considered this lead, so the '
                  'opener is not sent twice'))
    return True


def qualifies_for(lead) -> list:
    """
    The RUNNING drips this lead currently qualifies for, and why.

    Membership is never a mystery: the answer is always "because status is X".
    """
    if not lead or not lead.get('status'):
        return []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT campaign_id, name, is_running, accepted_statuses
                     FROM campaign_configs
                    WHERE type = 'drip' AND %s = ANY(accepted_statuses)
                    ORDER BY name""", (lead['status'],))
            return [dict(r, why=f"status is {lead['status']}")
                    for r in cur.fetchall()]


def sending_statuses() -> dict:
    """
    {status: [drip names]} for every status a RUNNING drip accepts.

    ⚠️ THE STATUS DROPDOWN IS A SEND CONTROL NOW. It used to mostly govern
    dialling, where a change is cheap. Membership is derived from it, so setting
    a lead to an accepted status can put mail on the wire within the hour, and
    every other control here that can do that says so before it is used.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT name, accepted_statuses FROM campaign_configs
                            WHERE type = 'drip' AND is_running
                            ORDER BY name""")
            out = {}
            for r in cur.fetchall():
                for st in r['accepted_statuses'] or []:
                    out.setdefault(st, []).append(r['name'])
            return out


def gate_counts(statuses=None) -> dict:
    """
    {status: how many leads have it} - so a gate change can be read BEFORE it is
    made. A checkbox whose effect is invisible until you save is a guess.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status, count(*) AS n FROM leads GROUP BY status')
            return {r['status']: r['n'] for r in cur.fetchall()}


def overlaps(campaign_id=None) -> list:
    """
    Statuses accepted by MORE THAN ONE running drip, with the drips that share
    them. A lead with such a status receives every one of those sequences.

    ⚠️ A WARNING, NOT A REFUSAL. Product news and a follow-up sequence are
    different conversations and both can be legitimate for one firm. The point is
    that it is a decision made at CONFIG time rather than a discovery made when a
    firm gets two emails in one afternoon.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT campaign_id, name, accepted_statuses
                             FROM campaign_configs
                            WHERE type = 'drip' AND is_running""")
            drips = [dict(r) for r in cur.fetchall()]
    by_status = {}
    for d in drips:
        for st in d['accepted_statuses'] or []:
            by_status.setdefault(st, []).append(d)
    out = []
    for st, ds in sorted(by_status.items()):
        if len(ds) < 2:
            continue
        if campaign_id and not any(str(d['campaign_id']) == str(campaign_id)
                                   for d in ds):
            continue
        out.append({'status': st, 'drips': ds,
                    'names': [d['name'] for d in ds]})
    return out


# ⚠️ `engaged` MEANS THEY REPLIED. A drip accepting it keeps emailing someone who
# answered, which is the opposite of what the reply-stop is for. That can be
# deliberate - a nurture sequence - so it WARNS rather than refusing.
#
# `clicked` is deliberately NOT here: a click is interest, not an answer, and a
# drip that drops a firm the moment it reads the sample is the worst version of
# this feature. `clicked` is also the natural gate for a warm-lead sequence.
CAUTION_STATUSES = {
    'engaged': ('they REPLIED - a drip accepting this keeps emailing someone who '
                'already answered, which is the opposite of the reply-stop. A '
                'click is `clicked`, not this: interest is not an answer'),
    'demo_pending': ('a demo is already booked with them - a cold sequence '
                     'reads badly at that point'),
    'human_review': 'these are flagged for you to look at, not to be mailed',
}


def record_send(cur, lead_id, step_id, seq, to_email, subject, sent_by,
                from_email=None):
    """
    One row per email that actually WENT, with its OWN click token.

    Returns the row. The token is per SEND, not per lead: that is what makes a
    click attributable to the step that produced it. If step 1 pulls every
    click the follow-ups are noise; if step 3 does, the opener needs rewriting.
    A per-lead count cannot tell those apart.
    """
    import secrets
    # ⚠️ REFUSES WITHOUT A RECIPIENT rather than writing ''. That coercion is
    # what migration 036 did, and it produced rows claiming a send to nobody -
    # which the roster then counted as a step delivered. email_sends_has_recipient
    # enforces this in the database; this is the same rule said in the caller's
    # own terms, so the message names the lead instead of a constraint.
    if not (to_email or '').strip():
        raise ValueError(f'lead {lead_id} has no email address - a send row '
                         f'without a recipient is not a send')
    # ⚠️ from_email IS WHAT THE CAPS COUNT ON. Without it a real send is invisible
    # to the hourly and daily limits - they would read zero forever and the pace
    # would exist only in the configuration.
    cur.execute(
        """INSERT INTO email_sends
               (lead_id, step_id, seq, to_email, subject, sent_by, from_email,
                click_token)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *""",
        (lead_id, step_id, seq, to_email.strip(), subject, sent_by,
         from_email, secrets.token_urlsafe(16)))
    return cur.fetchone()


def source_split(campaign_id) -> dict:
    """
    {'total': n, 'imported': n, 'call': n} for the leads on this drip.

    ⚠️ DERIVED, NOT DECLARED. A drip could carry a `source` column instead, and
    that was considered and rejected: step 1 is LOAD-BEARING for a call-sourced
    lead - it is the row email 1 is linked to - so hiding it would make the
    +4-day step become position 1, and enter() would link email 1 to THAT,
    skipping it. Hiding step 1 eats a step rather than skipping one.

    A count reflects what is actually on the drip rather than an intention set at
    creation, and it stays true when both sources are mixed - which nothing
    prevents, since bulk add-to-drip and default_drip_id can both feed one.
    Same reasoning as lead_source being provenance rather than the dial gate.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) AS total,
                          count(*) FILTER (WHERE lead_source = 'import') AS imported,
                          count(*) FILTER (WHERE lead_source <> 'import') AS call
                     FROM leads l
                     JOIN campaign_configs c ON c.campaign_id = %s
                    WHERE l.status = ANY(c.accepted_statuses)""", (campaign_id,))
            return dict(cur.fetchone())


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
# ⚠️ THE GATE. Membership is DERIVED from status, continuously - there is no
# assignment column and nothing routes a lead into a drip.
#
# The old model stored drip_campaign_id at email-1 time and the lead stayed there
# whatever happened next, so `status` and `drip_campaign_id` were two facts
# saying one thing and could disagree. A lead marked `engaged` was still queued
# for the next step, because nothing consulted the status. Guarding that needs a
# gate per disagreement; DERIVING membership makes the disagreement impossible.
#
# Re-evaluated on EVERY selection, which is what makes a status change take
# effect mid-sequence with nothing having to notice and act.
#
# accepted_statuses is a LIST: a drip may accept `emailed` and `max_attempts`
# together, and a lead matching ANY of them qualifies. Two running drips
# accepting one status BOTH send - deliberate, and warned about at config time
# rather than refused.
GATE = 'AND l.status = ANY(c.accepted_statuses)'
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
# STEP 1's clock runs from status_changed_at, NOT from emailed_at - emailed_at
# does not exist yet, because step 1 is what creates it. Steps 2+ run from
# emailed_at. Different events, so different anchors; see migration 040 for why
# that is not the duplicate-anchor fault rejected when the import was designed.
#
# status_changed_at is NOT NULL (backfilled and trigger-maintained), so no
# coalesce is needed and a NULL cannot silently exclude a lead:
# an absent entry time reads as "already elapsed", never as "never due", which
# is the direction that fails loudly rather than silently.
# ⚠️ STEP 1 IS NEVER SCHEDULED BY DAYS. It IS the first send, so "N days
# after the first send" is meaningless for it - and treating it as day 0
# from emailed_at is what let a CALL-SOURCED lead receive it.
#
# THE BUG THIS CLOSES: a lead that joined a drip before the sequence was
# written has no send row against step 1 (there was no step 1 to link to).
# Add the steps later and the days branch matched it - emailed_at set,
# delay_days 0 - so a firm we CALLED received step 1's cold opener, which
# says nothing about the call. enter() no longer mis-reports the link, but
# THIS is what makes it safe: position > 1 means step 1 cannot be reached
# down the days path at all, whatever happened at entry.
#
#   step 1     only for a lead with NO emailed_at, timed from status_changed_at
#   steps 2+   from emailed_at, which step 1 (or the call campaign) created
DUE_NOW = ("AND ((l.emailed_at IS NOT NULL AND s.position > 1"
           "      AND l.emailed_at + (s.delay_days || ' days')::interval"
           "          <= now())"
           "  OR (l.emailed_at IS NULL AND s.position = 1"
           "      AND l.status_changed_at"
           "          + (coalesce(s.delay_minutes, 0) || ' minutes')::interval"
           "          <= now()))")
# ⚠️ STEP 1 ANCHORS TO status_changed_at, not to an entry event. Joining a drip
# used to be an EVENT that stamped drip_entered_at; with membership derived
# there is no event - a lead simply qualifies or does not. The moment it BEGAN to
# qualify is the moment its status last changed, which is what a "N days after
# joining" delay has always meant. Maintained by trigger, because status is
# written from six places and a column depending on all of them remembering is a
# column that is wrong.

# ---------------------------------------------------------------------------
# PACING. Four layers, and they gate SELECTION - never the send.
#
# ⚠️ A CAPPED LEAD IS NOT-YET-DUE, NOT REFUSED. Putting these in send_step()
# would write a refused_ineligible audit row for every held lead on every
# 120-second tick: thousands of rows that read as failures, for leads that are
# simply waiting their turn. Sean's requirement is that anything past a cap
# WAITS - it must not fail and it must not vanish - and "not selected yet" is
# exactly that. The backlog is then derivable rather than stored: see held().
#
# Each fragment is its own constant so the break pass can remove exactly one and
# watch exactly the matching test go red.
# ---------------------------------------------------------------------------

# ⚠️ BUSINESS HOURS IN THE CONTACT'S TIMEZONE, REUSING THE CALL LOGIC VERBATIM.
# This is windows.PREFERENCE_WINDOW with one substitution: the drip's windows
# belong to the DRIP campaign, and with membership derived the drip in scope is
# `c` in the query itself - there is no drip column on the lead to key on. A derived string rather than a copy, because two copies of a
# timezone rule are two rules, and the first time they disagree one of them is
# mailing a firm at 4am.
#
# TCPA's LEGAL_WINDOW is deliberately NOT here: 8:00-20:30 is a law about
# telephone calls, and borrowing it for email would imply a legal constraint
# that does not exist. Business hours are an etiquette and reputation decision,
# and they live in campaign_windows where the operator can see them.
#
# ⚠️ A LEAD WITH NO TIMEZONE FALLS BACK TO THE OPERATOR'S. leads.timezone is
# NULLABLE since the email-only import (038), and `now() AT TIME ZONE NULL` is
# NULL, which fails every comparison - so without the coalesce an imported lead
# would never be due and would never say why. That is the silent-vanishing
# failure this whole feature is meant to avoid. The fallback is Sean's own
# hours, which is the best available proxy, and lead_source_split on the
# campaign screen shows how many leads are on it.
def _email_window() -> str:
    from api import windows
    return (windows.PREFERENCE_WINDOW
            .replace('w.campaign_id = l.campaign_id',
                     'w.campaign_id = c.campaign_id')
            .replace('l.timezone', "coalesce(l.timezone, %(op_tz)s)"))


# ⚠️ LIMITS ARE PER CAMPAIGN, COUNTS ARE PER MAILBOX. Reputation belongs to the
# ADDRESS. Two drips on info@counselorai.io at 15/hour each would put 30/hour on
# one mailbox, so both counts span every campaign sharing c.sender_email - which
# means the tighter campaign is bound by the shared total. Conservative on
# purpose: the wrong answer costs delay, the other wrong answer costs a domain.
_MAILBOX_SENDS = """
        SELECT count(*) FROM email_sends es
         WHERE es.from_email = c.sender_email
           AND es.sent_at IS NOT NULL
"""

HOURLY_CAP = f"""
    AND ({_MAILBOX_SENDS}
           AND es.sent_at > now() - interval '1 hour') < c.email_hourly_cap
"""

# The day boundary is the OPERATOR's, matching guards.assert_under_daily_cap.
# A rolling 24 hours would mean "50 a day" never refilled at a predictable time,
# and a per-contact-timezone day would make the cap unknowable from the screen.
DAILY_CAP = f"""
    AND ({_MAILBOX_SENDS}
           AND (es.sent_at AT TIME ZONE %(op_tz)s)::date
               = (now() AT TIME ZONE %(op_tz)s)::date) < c.email_daily_cap
"""

SELECT_DUE = """
    SELECT l.lead_id, l.company, l.dm_email, l.dm_name, l.emailed_at,
           s.step_id, s.position, s.delay_days, s.subject, s.body,
           c.campaign_id AS drip_campaign_id, c.name AS drip_name,
           (l.emailed_at + (s.delay_days || ' days')::interval) AS due_at
      FROM leads l
      -- NO JOIN KEY ON THE LEAD. Every drip is considered for every lead and the
      -- GATE below decides, which is what "membership is derived" means in SQL.
      JOIN campaign_configs c ON c.type = 'drip'
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
       {gate}
       {due_now}
       {pacing}
     -- The EARLIEST unsent due step for each lead, so a sequence cannot skip
     -- ahead if two fall due together after a pause.
     ORDER BY l.lead_id, s.position
"""


def _op_tz() -> str:
    """
    The operator's IANA timezone, for the daily-cap day boundary and as the
    fallback when a lead has none. Read from config rather than passed down,
    because every caller of due() would otherwise have to know about it - and a
    caller that forgot would get a NULL comparison and select nothing.
    """
    from api.config import load_config
    return load_config().OPERATOR_TIMEZONE


def _build_select(due_clause=DUE_NOW, pacing=True, pace_sql=None):
    """
    The selection query. `pacing=False` drops the four pace layers.

    ⚠️ upcoming() - the digest - MUST pass pacing=False. It answers "what is
    SCHEDULED", and a cap shifts when a mail goes out without changing whether
    it is coming. A digest that hid capped sends would under-report tomorrow,
    which is the one thing that block exists to prevent.
    """
    return SELECT_DUE.format(
        running=RUNNING_STOP, replied=REPLIED_STOP, archived=ARCHIVED_STOP,
        terminal=TERMINAL_STOP, do_not_send=DO_NOT_SEND_STOP,
        enabled=ENABLED_STOP, already_sent=ALREADY_SENT_STOP,
        gate=GATE, due_now=due_clause,
        pacing=(pace_sql if pace_sql is not None
                else ((HOURLY_CAP + DAILY_CAP + _email_window())
                      if pacing else '')))


def due(limit: int = 50):
    """
    (lead, step) pairs whose delay has elapsed - ONE per lead, the earliest
    unsent step.

    Selection is deliberately loose; every exclusion is re-checked inside the
    transaction that sends, the same lesson as dial_one and sender.send_one.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_build_select(), {'op_tz': _op_tz()})
            seen, out = set(), []
            for r in cur.fetchall():
                # ⚠️ ONE STEP PER LEAD PER DRIP, NOT ONE PER LEAD. Within a drip
                # the rule is unchanged: after a pause several steps can be due
                # and sending them all puts three emails in front of one firm.
                #
                # ACROSS drips it has to be per (lead, drip), or a lead
                # qualifying for two sequences would only ever receive the
                # earlier-due one and the other would starve. Overlap is
                # deliberate, so it has to be reachable.
                #
                # Safe only because pacing exists: the worker sends ONE email per
                # jittered gap, so two drips' mail to one firm is 60-300s apart
                # rather than simultaneous. Before the gap, this key would have
                # been a burst.
                key = (r['lead_id'], r['drip_campaign_id'])
                if key in seen:
                    continue
                seen.add(key)
                out.append(r)
                if len(out) >= limit:
                    break
            return out


def step_stats(campaign_id) -> list:
    """
    Per step: how many went out, how many were clicked, and the rate.

    ⚠️ THIS IS THE WHOLE REASON TO RUN A SEQUENCE - it says which email is doing
    the work and which one to cut. A per-lead click count cannot answer it, which
    is why the token is per SEND: email_sends is one row per (lead, step) with its
    own click_token, and email_clicks.send_id points back at it. So every click
    already resolves to a step and this table is a join, not a new capture.

    Counts are of SENT rows only. A prepared-but-unsent row (sent_at IS NULL) is
    a send that may still fail, and counting it would inflate the denominator and
    understate every rate on the page.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.step_id, s.position, s.subject, s.delay_days,
                       s.delay_minutes, s.enabled,
                       count(DISTINCT es.send_id)                 AS sent,
                       count(DISTINCT ec.send_id)                 AS clicked,
                       round(100.0 * count(DISTINCT ec.send_id)
                             / greatest(count(DISTINCT es.send_id), 1), 1) AS pct
                  FROM drip_steps s
                  LEFT JOIN email_sends es
                         ON es.step_id = s.step_id AND es.sent_at IS NOT NULL
                  LEFT JOIN email_clicks ec ON ec.send_id = es.send_id
                 WHERE s.campaign_id = %s AND s.deleted_at IS NULL
                 GROUP BY s.step_id, s.position, s.subject, s.delay_days,
                          s.delay_minutes, s.enabled
                 ORDER BY s.position""", (campaign_id,))
            return [dict(r) for r in cur.fetchall()]


# Statuses that end a sequence wherever the lead is in it. TERMINAL_STOP is the
# SQL form of the same list; this is what the roster shows a person.
_TERMINAL_STATUSES = ('dnc', 'bad_email', 'won', 'lost', 'demo_booked',
                      'unsubscribed', 'archived')


def _window_rows(campaign_id) -> dict:
    """{dow: (start_time, end_time)} for ENABLED days only."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT dow, start_time, end_time FROM campaign_windows
                            WHERE campaign_id = %s AND enabled""", (campaign_id,))
            return {r['dow']: (r['start_time'], r['end_time'])
                    for r in cur.fetchall()}


def next_open(at, tzname, windows, days_ahead: int = 14):
    """
    The first instant at or after `at` that falls inside an enabled window, in
    the LEAD's timezone. None when no day is enabled at all.

    ⚠️ THIS IS THE DIFFERENCE BETWEEN A SCHEDULE AND AN ANSWER. The raw delay
    arithmetic said a step was due at 1:38am - true, and outside every sending
    window, so the mail was never going at 1:38am. A column that reports the
    computation rather than the outcome makes the reader do the last step, and
    the whole point of business hours is that the last step is not obvious.

    Same reasoning as showing the call queue's real spacing instead of the
    configured interval.
    """
    from zoneinfo import ZoneInfo
    if not windows:
        return None
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        return None
    local = at.astimezone(tz)
    for _ in range(days_ahead + 1):
        # Postgres dow: 0 = Sunday, matching EXTRACT(dow) in PREFERENCE_WINDOW.
        dow = (local.weekday() + 1) % 7
        win = windows.get(dow)
        if win:
            start, end = win
            open_at = local.replace(hour=start.hour, minute=start.minute,
                                    second=0, microsecond=0)
            close_at = local.replace(hour=end.hour, minute=end.minute,
                                     second=0, microsecond=0)
            if local < open_at:
                return open_at.astimezone(datetime.timezone.utc)
            if local <= close_at:
                return local.astimezone(datetime.timezone.utc)
        # next day, at midnight local, and try again
        local = (local + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    return None


def roster(campaign_id, limit: int = 500) -> list:
    """
    The leads on this drip, with EMAIL facts only.

    No phone, no attempts, no agent score: on a drip those columns are noise, and
    a table nobody can scan is the same failure as a summary line that wraps every
    row onto three lines.

    `next_due` is the SCHEDULE, computed the same way DUE_NOW computes it - step 1
    from status_changed_at, later steps from emailed_at. It deliberately ignores the
    pace layers: a cap shifts when a mail goes out, and a roster that showed
    "next: never" for a lead behind a cap would be lying about the sequence.

    ⚠️ TWO QUESTIONS, TWO CTEs. "How far through THIS drip" and "when was this firm
    last emailed" are different, and answering both from one join is what made a
    firm that had received email 1 read as "—", never emailed. `own` counts this
    drip's steps; `any_send` counts every send to the lead whatever produced it.

    ⚠️ AND IT INCLUDES LEADS THAT NO LONGER QUALIFY, if this drip has sent them
    something. Filtering strictly on the gate meant a firm VANISHED from the page
    the moment it replied - mid-sequence, with no row and no reason - which is the
    same invisibility every other guard here exists to prevent. A lead that never
    received anything and does not qualify still does not appear: there is nothing
    to show.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                -- THIS DRIP's steps: the sequence number.
                WITH own AS (
                    SELECT es.lead_id, es.sent_at, st.position
                      FROM email_sends es
                      JOIN drip_steps st ON st.step_id = es.step_id
                     WHERE es.sent_at IS NOT NULL
                       AND st.campaign_id = %(cid)s
                ),
                per_lead AS (
                    SELECT lead_id, count(*) AS steps_sent,
                           max(position) AS last_step
                      FROM own GROUP BY lead_id
                ),
                -- ⚠️ EVERY SEND TO THE LEAD, whatever produced it. email 1 from a
                -- call campaign carries step_id NULL, so the join above drops it -
                -- which is how a firm that HAD been emailed read as "never".
                any_send AS (
                    SELECT es.lead_id, es.sent_at, es.send_id,
                           st.position, st.campaign_id AS step_campaign
                      FROM email_sends es
                      LEFT JOIN drip_steps st ON st.step_id = es.step_id
                     WHERE es.sent_at IS NOT NULL
                ),
                last_any AS (
                    SELECT lead_id, sent_at AS last_sent,
                           -- NAME WHAT IT WAS. "step 2" only when it is a step of
                           -- THIS drip; another drip's step is not this drip's
                           -- step 2, and calling it that would be a lie that reads
                           -- as fact.
                           CASE WHEN position IS NULL THEN 'email 1'
                                WHEN step_campaign = %(cid)s
                                     THEN 'step ' || position
                                ELSE 'another drip' END AS last_what
                      FROM (SELECT *, row_number() OVER (PARTITION BY lead_id
                                        ORDER BY sent_at DESC) AS rn
                              FROM any_send) x
                     WHERE rn = 1
                ),
                clicks AS (
                    SELECT s.lead_id, count(*) AS n,
                           string_agg(DISTINCT
                             CASE WHEN s.position IS NULL THEN 'email 1'
                                  WHEN s.step_campaign = %(cid)s
                                       THEN 'step ' || s.position
                                  ELSE 'another drip' END, ', ') AS by_step
                      FROM any_send s
                      JOIN email_clicks ec ON ec.send_id = s.send_id
                     GROUP BY s.lead_id
                ),
                total AS (
                    SELECT count(*) AS n FROM drip_steps
                     WHERE campaign_id = %(cid)s AND deleted_at IS NULL AND enabled
                ),
                nxt AS (
                    SELECT l.lead_id,
                           min(CASE WHEN st.position = 1
                                    THEN l.status_changed_at
                                         + (coalesce(st.delay_minutes, 0)
                                            || ' minutes')::interval
                                    ELSE l.emailed_at
                                         + (st.delay_days || ' days')::interval
                               END) AS due_at,
                           min(st.position) AS next_step,
                           -- the step_id that goes with that position, for SEND NOW
                           (array_agg(st.step_id ORDER BY st.position))[1]
                               AS next_step_id
                      FROM leads l
                      JOIN campaign_configs gc ON gc.campaign_id = %(cid)s
                      JOIN drip_steps st ON st.campaign_id = %(cid)s
                                        AND st.deleted_at IS NULL AND st.enabled
                     WHERE l.status = ANY(gc.accepted_statuses)
                       AND NOT EXISTS (SELECT 1 FROM email_sends es
                                        WHERE es.lead_id = l.lead_id
                                          AND es.step_id = st.step_id)
                     GROUP BY l.lead_id
                )
                SELECT l.lead_id, l.company, l.dm_name, l.dm_email, l.status,
                       l.emailed_at, l.replied_at, l.status_changed_at,
                       l.lead_source, l.timezone,
                       coalesce(p.steps_sent, 0) AS steps_sent,
                       la.last_sent, la.last_what, p.last_step,
                       (SELECT n FROM total) AS total_steps,
                       coalesce(cl.n, 0) AS clicks, cl.by_step AS clicked_steps,
                       n.due_at AS next_due, n.next_step, n.next_step_id,
                       EXISTS (SELECT 1 FROM email_do_not_send d
                                WHERE d.email = lower(btrim(l.dm_email)))
                           AS do_not_send
                  FROM leads l
                  LEFT JOIN per_lead p ON p.lead_id = l.lead_id
                  LEFT JOIN last_any la ON la.lead_id = l.lead_id
                  LEFT JOIN clicks   cl ON cl.lead_id = l.lead_id
                  LEFT JOIN nxt      n  ON n.lead_id = l.lead_id
                  JOIN campaign_configs gc2 ON gc2.campaign_id = %(cid)s
                 -- ⚠️ WHO IS ON THIS DRIP IS A QUESTION ABOUT STATUS. There is no
                 -- membership column to read; the roster asks the same question
                 -- the sender asks, so the two cannot disagree about who is here.
                 -- QUALIFIES NOW, **OR** THIS DRIP HAS SENT IT SOMETHING. The
                 -- second half is what keeps a firm visible after it replies
                 -- instead of vanishing mid-sequence with no row and no reason.
                 WHERE (l.status = ANY(gc2.accepted_statuses)
                        OR p.steps_sent IS NOT NULL)
                 ORDER BY coalesce(la.last_sent, l.status_changed_at)
                          DESC NULLS LAST
                 LIMIT %(lim)s""", {'cid': campaign_id, 'lim': limit})
            rows = [dict(r) for r in cur.fetchall()]
    return _with_drip_state(campaign_id, rows)


def _with_drip_state(campaign_id, rows) -> list:
    """
    Add `drip_state`, `state_detail` and `sends_at` to each roster row.

    ⚠️ THE STATUS COLUMN ON A DRIP MUST BE THE DRIP'S. leads.status is the CALL
    status - 'completed' means the dialer finished with the lead and says nothing
    at all about the sequence. On this screen the question is always "where is
    this lead in the sequence", and the answer has to come from the same
    exclusions due() applies, or the roster and the sender disagree.

    `sends_at` is when the mail will ACTUALLY go: the schedule advanced to the
    next open window, then through the pace queue. The raw due time is kept as
    `next_due` because the two differ and the difference is worth seeing.
    """
    from api import campaigns as _c
    camp = _c.get(campaign_id) or {}
    wins = _window_rows(campaign_id)
    op_tz = _op_tz()
    counts = sent_counts(campaign_id)
    now = datetime.datetime.now(datetime.timezone.utc)

    hourly_left = max(0, (counts.get('hourly_cap') or 0)
                      - (counts.get('sent_hour') or 0))
    daily_left = max(0, (counts.get('daily_cap') or 0)
                     - (counts.get('sent_today') or 0))
    gap = ((camp.get('email_gap_min_seconds', 60)
            + camp.get('email_gap_max_seconds', 300)) / 2.0) or 60

    # THE QUEUE, in the order due() returns them: earliest due first. A lead's
    # place in it is what decides when its mail goes, so the estimate has to be
    # built from the whole set rather than per row.
    ready = sorted(
        [r for r in rows
         if r['next_due'] and r['replied_at'] is None and not r['do_not_send']
         and r['status'] not in _TERMINAL_STATUSES],
        key=lambda r: r['next_due'])
    accepted = camp.get('accepted_statuses') or []
    slot = 0
    for r in rows:
        r['sends_at'] = None
        r['state_detail'] = ''
        r['why'] = []
        # ⚠️ A NUMBER IN THE NORMAL CASE, a word only when something ended or
        # blocked it. "waiting" repeated the schedule the next column already
        # gives and answered nothing; "0" says the drip has sent this lead
        # nothing, which is what a firm arriving from the caller actually is.
        n, total = r['steps_sent'], r['total_steps'] or 0
        r['progress'] = (str(n) if n in (0, 1) or not total
                         else f'{n} of {total}')
        if r['replied_at']:
            r['drip_state'], r['state_detail'] = 'stopped', 'replied'
        elif r['do_not_send']:
            r['drip_state'], r['state_detail'] = 'stopped', 'do not send'
        elif r['status'] in _TERMINAL_STATUSES:
            r['drip_state'], r['state_detail'] = 'stopped', r['status']
        elif r['status'] not in accepted:
            # IT WAS IN THIS DRIP AND IS NOT ANY MORE. Only reachable because the
            # roster keeps leads this drip has sent to - see roster().
            r['drip_state'] = 'stopped'
            r['state_detail'] = f"status is {r['status']}, which this drip does not accept"
        elif not r['next_due']:
            r['drip_state'] = 'done'
        elif not camp.get('is_running'):
            r['drip_state'], r['state_detail'] = 'paused', 'drip stopped'
        else:
            # 1. THE WINDOW. Never before business hours in the FIRM's timezone.
            # ⚠️ NAME EVERY LAYER THAT MOVED IT. A date with no reasoning is a
            # number to be trusted or doubted and nothing else; a held lead with
            # no reason is the same as no answer.
            step_no = r['next_step']
            if step_no == 1:
                mins = next((st['delay_minutes'] or 0)
                            for st in steps(campaign_id)
                            if st['position'] == 1)
                r['why'].append(
                    f'Step 1 sends {mins // 1440} day(s) after the status changed'
                    f' to {r["status"]}.' if mins else
                    'Step 1 sends as soon as the lead qualifies.')
            else:
                days = next((st['delay_days'] for st in steps(campaign_id)
                             if st['position'] == step_no), None)
                r['why'].append(
                    f'Step {step_no} sends {days} days after the first send.')

            at = next_open(max(r['next_due'], now),
                           r['timezone'] or op_tz, wins)
            if at is None:
                r['drip_state'] = 'held'
                r['state_detail'] = 'no business hours are enabled'
                r['why'].append('No business hours are enabled, so nothing can '
                                'go out at all.')
                continue
            if at > max(r['next_due'], now):
                r['why'].append(
                    'Moved to the next open window in the firm\'s timezone '
                    f'({r["timezone"] or op_tz}).')
            # 2. THE PACE. One email per gap, in queue order, inside the caps.
            if r['next_due'] <= now:
                i = ready.index(r) if r in ready else slot
                if i >= daily_left:
                    r['drip_state'] = 'held'
                    r['state_detail'] = "today's cap is spent"
                    r['why'].append(
                        f"Today's cap of {counts.get('daily_cap')} is spent "
                        f"({counts.get('sent_today')} sent), so it waits for "
                        f"tomorrow.")
                    at = next_open(now + datetime.timedelta(days=1),
                                   r['timezone'] or op_tz, wins) or at
                elif i >= hourly_left:
                    r['drip_state'] = 'held'
                    r['state_detail'] = 'hourly cap'
                    r['why'].append(
                        f"The hourly cap of {counts.get('hourly_cap')} is spent "
                        f"({counts.get('sent_hour')} sent this hour).")
                    at = max(at, now + datetime.timedelta(hours=1))
                else:
                    at = max(at, now + datetime.timedelta(seconds=gap * i))
                    r['drip_state'] = 'sending' if i == 0 else 'queued'
                    if i:
                        r['state_detail'] = f'{i} ahead of it'
                        r['why'].append(
                            f'{i} lead(s) ahead of it in the pace queue, one '
                            f'every {int(gap)}s.')
                slot += 1
            else:
                r['drip_state'] = 'waiting'
                if at > r['next_due']:
                    r['state_detail'] = 'outside business hours until then'
            r['sends_at'] = at
    # ⚠️ RENDERED IN THE FIRM'S OWN TIMEZONE, because the window that decides it
    # is the firm's. A UTC timestamp here reads as 4:00pm for a 9:00am send and
    # is nobody's clock - not the operator's and not the recipient's. The
    # operator's time comes with it, since "when will this send" is also a
    # question about their own day.
    for r in rows:
        r['sends_at_local'], r['sends_at_op'] = _both_clocks(
            r.get('sends_at'), r.get('timezone') or op_tz, op_tz)
        r['next_due_local'], _ = _both_clocks(
            r.get('next_due'), r.get('timezone') or op_tz, op_tz)
    return rows


def _both_clocks(at, tzname, op_tz):
    """(their time, your time) as short strings, or ('', '')."""
    if at is None:
        return '', ''
    from zoneinfo import ZoneInfo

    def _fmt(zone):
        try:
            local = at.astimezone(ZoneInfo(zone))
        except Exception:
            return at.strftime('%b %-d %-I:%M%p').lower() + ' UTC'
        return local.strftime('%b %-d %-I:%M%p').lower()

    theirs = _fmt(tzname)
    yours = _fmt(op_tz)
    return theirs, (yours if yours != theirs else '')


def held(campaign_id=None) -> dict:
    """
    What is due RIGHT NOW but held by pacing, and which layer is holding it.

    ⚠️ DERIVED, NEVER STORED. A capped send is a row that the selection does not
    return yet - there is no queue table, no status to get stuck in, and nothing
    to reconcile. The backlog is the difference between "scheduled and otherwise
    eligible" and "selectable now", which cannot drift from what the sender
    actually does because both sides are the same query.

    Sean's requirement was that anything past a cap WAITS: it must not fail and
    it must not silently vanish. This is the "does not vanish" half - the number
    on the campaign screen - and gating selection rather than the send is the
    "does not fail" half.
    """
    scope = ' AND c.campaign_id = %(cid)s' if campaign_id else ''
    params = {'op_tz': _op_tz(), 'cid': campaign_id}

    def _count(pace_sql):
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT count(DISTINCT l.lead_id) AS n FROM ('
                    + _build_select(pace_sql=pace_sql + scope)
                    + ') l', params)
                return cur.fetchone()['n']

    window = _email_window()
    scheduled = _count('')
    sendable = _count(HOURLY_CAP + DAILY_CAP + window)
    # Each layer measured ALONE, so the reason shown is the one actually biting.
    # They can overlap - a lead can be both outside hours and over the cap - so
    # these do not sum to `held`, and the screen must not present them as if
    # they do.
    out = {'scheduled': scheduled, 'sendable': sendable,
           'held': scheduled - sendable,
           'outside_hours': scheduled - _count(window),
           'over_hourly': scheduled - _count(HOURLY_CAP),
           'over_daily': scheduled - _count(DAILY_CAP)}
    out.update(sent_counts(campaign_id))
    return out


def sent_counts(campaign_id=None) -> dict:
    """
    What this MAILBOX has actually sent in the last hour and today.

    Per mailbox, not per campaign, for the same reason the caps are: the number
    that matters to a spam filter is what the ADDRESS sent.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.sender_email, c.email_hourly_cap, c.email_daily_cap,
                       c.email_gap_min_seconds, c.email_gap_max_seconds
                  FROM campaign_configs c
                 WHERE c.campaign_id = %s""", (campaign_id,))
            camp = cur.fetchone()
            if camp is None:
                return {'sent_hour': 0, 'sent_today': 0}
            cur.execute("""
                SELECT count(*) FILTER (
                         WHERE es.sent_at > now() - interval '1 hour') AS hour,
                       count(*) FILTER (
                         WHERE (es.sent_at AT TIME ZONE %(tz)s)::date
                             = (now() AT TIME ZONE %(tz)s)::date) AS today
                  FROM email_sends es
                 WHERE es.from_email = %(se)s
                   AND es.sent_at IS NOT NULL""",
                {'tz': _op_tz(), 'se': camp['sender_email']})
            n = cur.fetchone()
            return {'sent_hour': n['hour'] or 0, 'sent_today': n['today'] or 0,
                    'hourly_cap': camp['email_hourly_cap'],
                    'daily_cap': camp['email_daily_cap'],
                    'gap_min': camp['email_gap_min_seconds'],
                    'gap_max': camp['email_gap_max_seconds']}


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
            cur.execute(_build_select(clause, pacing=False),
                        {'h': within_hours})
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

def row_for_send(campaign_id, lead_id, step_id):
    """
    The row send_step expects, for ONE lead and ONE step, ignoring the schedule.

    ⚠️ THE SAME SHAPE due() RETURNS, deliberately. SEND NOW skips only the timing,
    so it must hand send_step exactly what selection would have handed it - then
    every exclusion inside that transaction applies unchanged. A hand-rolled send
    with its own guard list is how one gets forgotten.

    None when the step is not on this drip or the lead has no address: refused in
    the caller's terms rather than as a constraint violation deeper down.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.lead_id, l.company, l.dm_email, l.dm_name, l.emailed_at,
                       s.step_id, s.position, s.delay_days, s.subject, s.body,
                       c.campaign_id AS drip_campaign_id, c.name AS drip_name,
                       now() AS due_at
                  FROM leads l
                  JOIN campaign_configs c ON c.campaign_id = %s
                  JOIN drip_steps s ON s.campaign_id = c.campaign_id
                                   AND s.step_id = %s AND s.deleted_at IS NULL
                 WHERE l.lead_id = %s
                   AND btrim(coalesce(l.dm_email, '')) <> ''""",
                (campaign_id, step_id, lead_id))
            row = cur.fetchone()
            return dict(row) if row else None


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
                # THE DRIP IS THE ONE THAT SELECTED THIS ROW. A lead may
                # qualify for several, so "the lead's drip" is not a thing that
                # exists any more.
                camp = campaigns.get(row.get('drip_campaign_id'))

                # 1. THE GATE. The same seven exclusions email 1 uses, reused
                #    verbatim - the brief requires them on EVERY step, not just
                #    the first. `step=True` swaps the email-1 mode check for the
                #    drip's own switch; nothing else differs.
                # ⚠️ STEP 1 OF A LEAD THAT ALREADY HAD EMAIL 1 IS A LINK, NOT
                # A SEND. This is enter()'s surviving half, moved from assignment
                # time to selection time. Without it a call-sourced lead receives
                # the cold opener a second time - step 1 unsent, delay_days 0,
                # emailed_at set, nothing excluding it.
                # ⚠️ ATTRIBUTE EMAIL 1 TO STEP 1 BEFORE SENDING ANYTHING ELSE.
                # This is enter()'s surviving half, and it runs on whichever step
                # is selected first - NOT on step 1, because DUE_NOW never selects
                # position 1 for a lead that already has emailed_at. That is also
                # why it cannot cause a duplicate opener, and why believing it
                # could was wrong.
                #
                # Unlinked, email 1's row carries step_id NULL forever: step 1
                # reads "0 sent" for every call-sourced lead, and _maybe_finish
                # counts done steps by joining to drip_steps, so `done < total`
                # holds permanently and the lead never leaves the drip.
                if lead.get('emailed_at'):
                    _first = steps(row['drip_campaign_id'])
                    if _first:
                        link_email_1(cur, lead_id, _first[0]['step_id'])

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
                                       f'auto:drip:{cfg.SENDER_DOMAIN}',
                                       from_email=(camp or {}).get('sender_email'))
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
            _maybe_finish(cfg, lead_id, row['drip_campaign_id'])
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


def _maybe_finish(cfg, lead_id, drip_campaign_id) -> bool:
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
            cur.execute("""SELECT %(cid)s::uuid AS drip_campaign_id,
                                  -- ENABLED ONLY. A disabled tail step would
                                  -- otherwise never be 'done', so the sequence
                                  -- would never terminate and the lead would
                                  -- sit in the drip forever - the limbo the
                                  -- whole model refuses.
                                  (SELECT count(*) FROM drip_steps s
                                    WHERE s.campaign_id = %(cid)s
                                      AND s.deleted_at IS NULL
                                      AND s.enabled)             AS total,
                                  (SELECT count(*) FROM email_sends es
                                    JOIN drip_steps s2 ON s2.step_id = es.step_id
                                   WHERE es.lead_id = l.lead_id
                                     AND s2.campaign_id = %(cid)s
                                     AND s2.deleted_at IS NULL
                                     AND s2.enabled)            AS done
                             FROM leads l WHERE l.lead_id = %(lid)s""",
                        {'cid': drip_campaign_id, 'lid': lead_id})
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

# ⚠️ WHICH STATUS EACH STOP MEANS. With membership derived from status, stopping
# a lead IS setting a status no running drip accepts - there is no assignment to
# clear. Mapping rather than a single "stopped" value because the reason has to
# survive: six months on, "it said no" and "it never answered" are different
# facts and only the status records which.
STOP_STATUS = {
    'replied': 'engaged',
    'bounced': 'bad_email',
    'unsubscribed': 'archived',
    'demo_booked': 'demo_booked',
    'no_reply': 'lost_no_response',
    # A hand stop has no outcome to record, so it parks the lead: out of every
    # gate, still visible, still workable.
    'by_hand': 'paused',
}


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
            cur.execute("""SELECT status, dm_email FROM leads
                            WHERE lead_id = %s FOR UPDATE""", (lead_id,))
            row = cur.fetchone()
            if row is None:
                return False
            # ⚠️ STOPPING A LEAD IS A STATUS CHANGE NOW, because membership is
            # derived from status and there is nothing else to clear. Each reason
            # maps to the status that MEANS it, so the stop and the pipeline
            # cannot disagree - and the lead falls out of every drip whose gate
            # no longer matches, which is the same mechanism doing the work
            # rather than a second one.
            #
            # The status carries the reason, so a lead is never "stopped, but
            # nobody can say why" - the fault archive_reason exists to prevent.
            new_status = STOP_STATUS.get(reason)
            if new_status:
                cur.execute(
                    """UPDATE leads SET status = %s, updated_at = now()
                        WHERE lead_id = %s""", (new_status, lead_id))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s,'drip',%s,%s)""",
                (lead_id, f'drip STOPPED ({reason})', f'by {by}'))

    if reason == 'unsubscribed' and row['dm_email']:
        archive.do_not_send(row['dm_email'], 'unsubscribed', f'drip:{by}')
        archive.archive(lead_id, 'unsubscribed', by=by)
    elif reason == 'bounced' and row['dm_email']:
        archive.do_not_send(row['dm_email'], 'bad_email', f'drip:{by}')
        # THE STATUS IS ALREADY 'bad_email' - STOP_STATUS set it above, in the
        # same transaction that recorded the stop. A second UPDATE here used to do
        # it and is now DEAD CODE: removing it changed nothing, which is exactly
        # what break 105 reported when it went GREEN on the full pass.
        #
        # The property it protected has not moved: the address going on the
        # do-not-send list is what stops us mailing it again, and it is not what
        # tells anyone it happened. Without a visible status the lead sits at
        # 'emailed' with no explanation - in no filter, on no queue, simply
        # stopped. A LEAD FAILING SILENTLY is the shape every other guard here
        # exists to prevent. That guard is now STOP_STATUS['bounced'], and break
        # 105 points at it.
        #
        # NOT archived, deliberately: a bounce is a bad ADDRESS, not a bad firm,
        # and it usually wants a corrected one - a person's job, which needs the
        # lead in front of them rather than resting for six months.
        with db.get_conn() as conn:
            with conn.cursor() as cur:
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
