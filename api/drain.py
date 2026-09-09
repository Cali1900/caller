"""
The webhook drain. All real work happens here, off the durable inbox.

THE THREE TRAPS, and how this module handles each:

1. call_analysis lives on `call_analyzed`, NOT on `call_ended`. The
   call_ended payload has no call_analysis at all, so reading the extraction
   fields there returns null and reads as "extraction is broken" rather than
   "wrong event". _handle_call_ended writes ONLY telephony facts; every
   extracted field is read in _handle_call_analyzed.

2. Calls that never connect (dial_failed / dial_no_answer / dial_busy) skip
   call_started but still fire call_ended AND call_analyzed. The state machine
   must take a lead from `dialing` to `no_answer` with no conversation and no
   analysis. _no_conversation() detects this and the analyzed branch short
   circuits to the retry ladder.

3. Custom analysis fields are ABSENT when no conversation happened. Reading
   them blindly writes the string "undefined" into dm_email. _field() returns
   None for absent, empty, and for the literal strings a JSON bridge produces
   ("undefined", "null", "None"). Nothing else in this module reads the
   analysis dict directly.
"""

import json

from api import db, retry_ladder, stages

# THE RETRY LADDERS LIVE ON THE CAMPAIGN, not here. They used to be a flat
# dict: busy 15m and no_answer 2h regardless of attempt, so four attempts
# meant four calls to the same firm inside eight hours - a pattern a
# receptionist notices, and the opposite of what the spacing work was for.
#
# See api/retry_ladder.py for the rung grammar. What stays here is only the
# mapping from a Retell disconnection reason to a ladder, which is a fact
# about the platform rather than a preference of Sean's.
#
# MAX_ATTEMPTS is now per campaign too, because a rung only fires if an
# attempt follows it - a four-rung ladder under a hardcoded cap of 4 had a
# fourth rung that could never fire.
MAX_ATTEMPTS = 4          # fallback only, when the campaign cannot be read

# disconnection_reason -> retry reason
REASON_MAP = {
    'dial_busy': 'busy',
    'dial_no_answer': 'no_answer',
    'dial_failed': 'no_answer',
    'voicemail_reached': 'voicemail',
    'dial_answered_machine': 'voicemail',
}

# Reasons that mean the phone never got answered by a human.
NO_CONNECT_REASONS = {
    'dial_failed', 'dial_no_answer', 'dial_busy', 'no_answer',
    'voicemail_reached', 'dial_answered_machine',
}

# The same set as a SQL array literal, DERIVED from it rather than restated.
#
# _LEAD_JOINS binds its parameters positionally and psycopg2 refuses a query
# that mixes positional with named placeholders, so the lead query cannot
# pass this set as a parameter without converting every other filter too.
# Building the literal from the set keeps one source of truth: adding a
# reason above changes the SQL, and nothing here is caller supplied.
NO_CONNECT_SQL = "ARRAY[" + ', '.join(
    "'" + r.replace("'", "''") + "'" for r in sorted(NO_CONNECT_REASONS)) + "]"

_JUNK_STRINGS = {'undefined', 'null', 'none', 'n/a', 'na', '-'}


# ---------------------------------------------------------------------------
# safe readers
# ---------------------------------------------------------------------------

def _field(analysis, name):
    """
    Read one custom analysis field.

    Returns None for: absent key, null, empty string, and the literal junk a
    JSON/JS bridge produces when a value was never set. This is the ONLY way
    this module reads extracted fields - see trap 3.
    """
    if not isinstance(analysis, dict):
        return None
    data = analysis.get('custom_analysis_data')
    if not isinstance(data, dict):
        data = analysis
    if name not in data:
        return None
    v = data[name]
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == '' or s.lower() in _JUNK_STRINGS:
            return None
        return s
    return v


def _bool_field(analysis, name):
    """Booleans specifically: absent stays None, so 'unknown' != 'false'."""
    v = _field(analysis, name)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('true', 'yes', '1')


def _no_conversation(call: dict, analysis) -> bool:
    """Trap 2: the call never reached a human."""
    reason = (call.get('disconnection_reason') or '').strip()
    if reason in NO_CONNECT_REASONS:
        return True
    if not call.get('transcript') and not analysis:
        return True
    return False


def _resolve_lead_id(conn, call: dict):
    """
    metadata.lead_id first, last_call_id second.

    Matching on leads.last_call_id ALONE breaks the moment a lead is re-dialed
    before its previous webhooks have drained - the older events would land on
    the newer call. metadata is stamped at creation and never moves.
    """
    meta = call.get('metadata')
    candidate = meta.get('lead_id') if isinstance(meta, dict) else None

    with conn.cursor() as cur:
        # The metadata id must be CONFIRMED against the table. Trusting it
        # blindly means a deleted lead - or a stale/forged metadata block -
        # produces a foreign key violation on the calls insert, which then
        # fails five times and parks as a poison message. Found exactly that
        # way against the running container.
        if candidate:
            try:
                cur.execute(
                    'SELECT lead_id FROM leads WHERE lead_id = %s', (candidate,)
                )
            except Exception:
                # not even a valid uuid
                cur.execute('SELECT NULL AS lead_id WHERE false')
            row = cur.fetchone()
            if row:
                return str(row['lead_id'])

        cur.execute(
            'SELECT lead_id FROM leads WHERE last_call_id = %s',
            (call.get('call_id'),),
        )
        row = cur.fetchone()
    return str(row['lead_id']) if row else None


# ---------------------------------------------------------------------------
# call_ended - telephony facts only
# ---------------------------------------------------------------------------

def _handle_call_ended(conn, call: dict) -> None:
    lead_id = _resolve_lead_id(conn, call)
    if lead_id is None:
        # Nothing to attach it to. Do not invent a lead.
        return

    # Stamp EXACTLY which agent version ran, from the metadata the dialer set.
    meta = call.get('metadata') if isinstance(call.get('metadata'), dict) else {}
    agent_version = meta.get('agent_version')

    start_ms, end_ms = call.get('start_timestamp'), call.get('end_timestamp')
    duration = None
    if isinstance(start_ms, int) and isinstance(end_ms, int):
        duration = end_ms - start_ms

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO calls (
                call_id, lead_id, stage, agent_id,
                started_at, ended_at, duration_ms,
                disconnection_reason, call_status,
                transcript, recording_url, latency, agent_version
            )
            VALUES (
                %s, %s,
                COALESCE((SELECT stage FROM leads WHERE lead_id = %s), 'L1'),
                %s,
                to_timestamp(%s / 1000.0), to_timestamp(%s / 1000.0), %s,
                %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (call_id) DO UPDATE SET
                ended_at             = EXCLUDED.ended_at,
                duration_ms          = EXCLUDED.duration_ms,
                disconnection_reason = EXCLUDED.disconnection_reason,
                call_status          = EXCLUDED.call_status,
                transcript           = EXCLUDED.transcript,
                recording_url        = EXCLUDED.recording_url,
                latency              = EXCLUDED.latency,
                agent_version        = COALESCE(EXCLUDED.agent_version, calls.agent_version)
            """,
            (
                call.get('call_id'), lead_id, lead_id, call.get('agent_id'),
                start_ms, end_ms, duration,
                call.get('disconnection_reason'), call.get('call_status'),
                call.get('transcript'), call.get('recording_url'),
                # Latency logged from day one: it is what later tells us
                # whether P50 is our prompt or the platform floor.
                json.dumps(call.get('latency')) if call.get('latency') else None,
                agent_version,
            ),
        )
        # Cost is IN the payload - capture it rather than estimate it later.
        cost = call.get('call_cost') or {}
        if cost:
            cur.execute(
                """UPDATE calls SET cost_cents = %s, cost_breakdown = %s
                    WHERE call_id = %s""",
                (cost.get('combined_cost'), json.dumps(cost), call.get('call_id')))


# ---------------------------------------------------------------------------
# call_analyzed - the one that matters
# ---------------------------------------------------------------------------

def _retry(conn, lead_id: str, reason: str) -> None:
    """
    attempts+1, then wait the rung this campaign sets for this outcome.

    THE LADDER IS THE LEAD'S OWN CAMPAIGN'S, re-read here in-transaction. The
    same fault as the stale campaign snapshot on dial: a lead moved between
    campaigns, or a ladder edited mid-run, must take effect on the next
    retry rather than whenever the worker last looked.

    Every value is BOUND. The fragment retry_ladder.sql_for returns contains
    no caller data - it is one of exactly two shapes, and the duration or the
    hour inside it is a parameter.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT l.attempts, c.max_attempts,
                              c.retry_busy, c.retry_no_answer, c.retry_voicemail
                         FROM leads l
                    LEFT JOIN campaign_configs c ON c.campaign_id = l.campaign_id
                        WHERE l.lead_id = %s""", (lead_id,))
        row = cur.fetchone()
        attempts = (row['attempts'] if row else 0) + 1
        cap = (row or {}).get('max_attempts') or MAX_ATTEMPTS
        ladder = retry_ladder.for_campaign(row, reason)
        try:
            frag, frag_params = retry_ladder.sql_for(
                retry_ladder.rung_for(ladder, attempts))
        except retry_ladder.BadLadder as exc:
            # FAIL SLOW, never fast. An unreadable rung must not become a
            # tight gap; it becomes the widest rung in the default ladders.
            # UNKNOWN MUST NEVER DIAL FASTER THAN CONFIGURED - the same
            # property worker.next_gap() holds for spacing.
            print(f'[drain] bad retry rung for {reason}: {exc} - '
                  f'falling back to {retry_ladder.FALLBACK_RUNG}', flush=True)
            frag, frag_params = retry_ladder.sql_for(retry_ladder.FALLBACK_RUNG)

        if attempts >= cap:
            cur.execute(
                """UPDATE leads SET status='max_attempts', attempts=%s,
                       last_outcome=%s, updated_at=now()
                    WHERE lead_id=%s""",
                (attempts, reason, lead_id))
            return

        cur.execute(
            f"""UPDATE leads l
                   SET status='no_answer', attempts=%s, last_outcome=%s,
                       next_attempt_at = {frag},
                       updated_at=now()
                 WHERE l.lead_id=%s""",
            [attempts, reason] + frag_params + [lead_id])


def _activity(conn, lead_id, call_id, summary, detail=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO activity (lead_id, kind, call_id, summary, detail)
               VALUES (%s, 'call', %s, %s, %s)""",
            (lead_id, call_id, summary, detail),
        )


def _handle_call_analyzed(conn, call: dict) -> None:
    lead_id = _resolve_lead_id(conn, call)
    if lead_id is None:
        return

    call_id = call.get('call_id')
    analysis = call.get('call_analysis')

    with conn.cursor() as cur:
        cur.execute(
            'UPDATE calls SET analysis = %s WHERE call_id = %s',
            (json.dumps(analysis) if analysis else None, call_id),
        )

    # Trap 2: never connected. No analysis will ever arrive for this call.
    if _no_conversation(call, analysis):
        reason = REASON_MAP.get((call.get('disconnection_reason') or '').strip(),
                                'no_answer')
        _retry(conn, lead_id, reason)
        _activity(conn, lead_id, call_id, 'no answer',
                  call.get('disconnection_reason'))
        return

    # Trap 3: every read below goes through _field/_bool_field.
    disposition = _field(analysis, 'gatekeeper_disposition')
    name = _field(analysis, 'decision_maker_name')
    title = _field(analysis, 'decision_maker_title')
    email = _field(analysis, 'email_address')
    confirmed = _bool_field(analysis, 'email_spelled_back_confirmed')
    cb_requested = _bool_field(analysis, 'callback_requested')
    cb_when = _field(analysis, 'callback_when')
    cb_person = _field(analysis, 'callback_person')
    # L3 only: the whole point of the follow-up call. NULL means never asked,
    # which is different from "no" - phase 6 reads this as one of its gates.
    # Her own name, if she offered it. NEVER a required ask - it exists so the
    # follow-up email can reference the call instead of reading as cold
    # outreach, which is the whole value of having made the call.
    gatekeeper = _field(analysis, 'gatekeeper_name')
    if gatekeeper:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE leads SET gatekeeper_name = COALESCE(%s, gatekeeper_name),'
                ' updated_at = now() WHERE lead_id = %s', (gatekeeper, lead_id))

    # HOW MANY DEMANDS A MONTH. Asked only after a name AND an email, and
    # dropped immediately if she does not know - so an absent field is the
    # normal case, not a failure.
    #
    # The VERBATIM is stored whatever happens; the number is parsed only where
    # one is clearly stated. NULL means she did not answer and must never be
    # read as zero - a firm that declined is not a firm that sends none.
    volume_raw = _field(analysis, 'demands_per_month')
    if volume_raw:
        from api import volume as volume_mod
        n, note = volume_mod.parse(volume_raw)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET demands_per_month_raw = %s,
                          demands_per_month = COALESCE(%s, demands_per_month),
                          updated_at = now()
                    WHERE lead_id = %s""", (volume_raw, n, lead_id))
        _activity(conn, lead_id, call_id,
                  'demand volume captured'
                  + ('' if n is not None else ' (no number - %s)' % note),
                  f'"{volume_raw}"' + (f' -> {n}/month' if n is not None else ''))

    saw_email = _bool_field(analysis, 'decision_maker_saw_email')
    next_step = _field(analysis, 'best_next_step')

    if saw_email is not None or next_step is not None:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET dm_saw_email = COALESCE(%s, dm_saw_email),
                          best_next_step = COALESCE(%s, best_next_step),
                          updated_at = now()
                    WHERE lead_id = %s""",
                (saw_email, next_step, lead_id))

    # A NAME IS A WIN even when the call otherwise failed - store it
    # regardless of which branch we take below. That is half the objective,
    # banked, and it is why these writes come before the branch.
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE leads
                  SET dm_name  = COALESCE(%s, dm_name),
                      dm_title = COALESCE(%s, dm_title),
                      updated_at = now()
                WHERE lead_id = %s""",
            (name, title, lead_id),
        )

    if disposition == 'remove_me':
        # Suppression row and the lead going dnc happen in ONE transaction.
        # Not a follow-up job, not a nightly sync.
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO suppression (phone_e164, reason, source)
                   SELECT phone_e164, 'requested', %s FROM leads WHERE lead_id = %s
                   ON CONFLICT (phone_e164) DO NOTHING""",
                (call_id, lead_id),
            )
            cur.execute(
                "UPDATE leads SET status='dnc', updated_at=now() WHERE lead_id=%s",
                (lead_id,),
            )
        _activity(conn, lead_id, call_id, 'asked to be removed - suppressed')
        return

    if disposition == 'send_email':
        # A LANE, NOT A FAILURE. Capture the address and stop calling.
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET status='email_path',
                          dm_email = COALESCE(%s, dm_email),
                          dm_email_confirmed = COALESCE(%s, dm_email_confirmed),
                          updated_at = now()
                    WHERE lead_id = %s""",
                (email, confirmed, lead_id),
            )
        _activity(conn, lead_id, call_id, 'send-email path', email)
        return

    if disposition == 'refused':
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE leads SET status='completed', updated_at=now() WHERE lead_id=%s",
                (lead_id,),
            )
        _activity(conn, lead_id, call_id, 'refused - not retrying')
        return

    if disposition == 'callback' or cb_requested:
        _handle_callback(conn, lead_id, call_id, cb_person, cb_when)
        return

    if disposition == 'voicemail':
        _retry(conn, lead_id, 'voicemail')
        _activity(conn, lead_id, call_id, 'voicemail')
        return

    # gave_info, or a disposition we do not recognise but with data attached.
    if email:
        # Unconfirmed spelling goes to human_review rather than completed:
        # a wrong email is a dead lead that looks like a live one, and the
        # leads_needs_you index keys off this status.
        status = 'completed' if confirmed else 'human_review'
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads
                      SET status = %s,
                          dm_email = %s,
                          dm_email_confirmed = %s,
                          dm_email_source = %s,
                          updated_at = now()
                    WHERE lead_id = %s""",
                (status, email, bool(confirmed), call.get('transcript'), lead_id),
            )
        _activity(conn, lead_id, call_id,
                  f'email captured ({"confirmed" if confirmed else "UNCONFIRMED"})',
                  email)
        # L1 -> L2 the moment a CONFIRMED email lands, in this same
        # transaction. The capture and the stage move must commit together:
        # a lead sitting at L1 with a confirmed email is a state nobody can
        # reason about, and it would never be picked up for a follow-up.
        if confirmed:
            with conn.cursor() as cur:
                stages.advance_to_l2(cur, lead_id, f'confirmed on call {call_id}')
        return

    if name:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE leads SET status='completed', updated_at=now() WHERE lead_id=%s",
                (lead_id,),
            )
        _activity(conn, lead_id, call_id, 'name only, no email', name)
        return

    _retry(conn, lead_id, 'no_info')
    _activity(conn, lead_id, call_id, 'no information obtained')


def _handle_callback(conn, lead_id, call_id, person, when_text) -> None:
    """
    Phase 1 keeps this deliberately simple: mark the callback, bank the name,
    and push next_attempt_at out by 2 hours so the lead is not re-dialed
    immediately.

    Resolving free text like "next Tuesday" into a real timestamp, clamping it
    into the calling window, and the 21-day flag are PHASE 2 - they need the
    window query, which does not exist yet. What matters now is that a
    callback never loops.
    """
    with conn.cursor() as cur:
        cur.execute('SELECT callback_count FROM leads WHERE lead_id=%s', (lead_id,))
        row = cur.fetchone()
        count = (row['callback_count'] if row else 0) + 1

        if count > 3:
            # Three "call back later"s is politeness, not interest.
            cur.execute(
                """UPDATE leads SET status='completed', callback_count=%s,
                   updated_at=now() WHERE lead_id=%s""",
                (count, lead_id),
            )
            return

        cur.execute(
            """UPDATE leads
                  SET status='callback',
                      callback_person = COALESCE(%s, callback_person),
                      callback_count = %s,
                      next_attempt_at = now() + interval '2 hours',
                      updated_at = now()
                WHERE lead_id = %s""",
            (person, count, lead_id),
        )
    _activity(conn, lead_id, call_id, 'callback requested', when_text)


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

HANDLERS = {
    'call_ended': _handle_call_ended,
    'call_analyzed': _handle_call_analyzed,
    # call_started is optional; recorded in the inbox, nothing to do.
    'call_started': lambda conn, call: None,
}


def _drain_one() -> bool:
    """Returns True if an event was handled. One event per transaction."""
    claimed = None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT call_id, event, payload
                      FROM webhook_events
                     WHERE processed_at IS NULL
                       AND attempts < 5
                     ORDER BY received_at
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row is None:
                    return False
                claimed = (row['call_id'], row['event'])

                handler = HANDLERS.get(row['event'])
                if handler is not None:
                    payload = row['payload']
                    handler(conn, payload.get('call') or {})

                cur.execute(
                    """UPDATE webhook_events SET processed_at = now()
                        WHERE call_id = %s AND event = %s""",
                    claimed,
                )
        return True
    except Exception as exc:
        if claimed is None:
            raise
        # Separate transaction: the failed work is rolled back, but the
        # attempt is recorded so a poison message cannot spin forever.
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE webhook_events
                          SET attempts = attempts + 1, last_error = %s
                        WHERE call_id = %s AND event = %s""",
                    (f'{type(exc).__name__}: {exc}'[:2000], *claimed),
                )
        print(f'[drain] {claimed[1]} {claimed[0]} failed: {exc}', flush=True)
        return True


def drain_once(limit: int = 50) -> int:
    n = 0
    while n < limit and _drain_one():
        n += 1
    return n
