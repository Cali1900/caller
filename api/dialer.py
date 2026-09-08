"""
The dialer loop.

Order is: select -> claim -> guard -> dial. Each step exists because the one
before it cannot do its job.

Everything that can stop a call is either a named SQL fragment in the
selection query or a named assert_* before the dial, so the break pass can
remove exactly one and watch exactly one test go red:

    SUPPRESSION_JOIN            a suppressed number is never a candidate
    windows.LEGAL_WINDOW        TCPA 8:00-20:30 in the CALLED PARTY's time
    windows.PREFERENCE_WINDOW   the operator's hours, also client-local
    CAMPAIGN_JOIN               nothing dials outside a started campaign
    assert_not_suppressed       re-check, for a number suppressed mid-batch
    assert_dialable             the allowlist
    assert_campaign_running     exactly one campaign runs; none by default
    assert_under_daily_cap      the CAMPAIGN's new-leads-per-day cap
    QUEUE_MEMBERSHIP            pool_status='active' - queued
    CAMPAIGN_MEMBERSHIP         only the RUNNING campaign's leads dial
"""

import time

from api import campaigns, db, retell, windows
from api.config import load_config
from api.guards import (DialRefused, assert_campaign_running, assert_dialable,
                        assert_not_suppressed, assert_under_daily_cap)

# A suppressed number must never be a CANDIDATE, not merely never dialed.
SUPPRESSION_JOIN = (
    'AND NOT EXISTS (SELECT 1 FROM suppression s '
    'WHERE s.phone_e164 = l.phone_e164)'
)

# L2 NEVER DIALS. At L2 we owe them an email and have not sent it; calling
# would ask a question we are about to answer ourselves. Isolated as a
# constant so removing it is a single, visible edit.
STAGE_DIALABLE = "AND l.stage IN ('L1', 'L3')"

# A REPLY STOPS THE FOLLOW-UP DEAD. Nothing sets replied_at yet - the coming
# sequencer from demandcounselor.com owns reply detection - but the guard
# lives in the selection query from the start so the sender slots in without
# touching the dialer.
REPLIED_GUARD = "AND l.replied_at IS NULL"

# THE STANDING QUEUE. A lead is queued when pool_status='active' - that is
# what "add to campaign" sets. There is no per-day campaign and no enrol step.
#
# Uploading does not queue. Queueing does not dial. Only STARTING A
# CAMPAIGN dials, and that is a deliberate act on /campaigns.
QUEUE_MEMBERSHIP = "AND l.pool_status = 'active'"

# A lead only dials for the campaign that is RUNNING. Leads assigned to any
# other campaign sit idle - that is the whole point of naming them.
CAMPAIGN_MEMBERSHIP = "AND l.campaign_id = %(campaign_id)s"

SELECT_DUE = """
    SELECT l.lead_id, l.phone_e164, l.status, l.stage, l.attempts,
           l.company, l.dm_name, l.dm_title, l.dm_email, l.emailed_at,
           l.callback_person, l.first_dialed_at,
           CASE WHEN l.first_dialed_at IS NULL THEN 'fresh' ELSE 'carryover' END
             AS source
      FROM leads l
     WHERE true
       {queue}
       {campaign}
       AND l.status IN ('new', 'callback', 'no_answer', 'queued')
       AND l.next_attempt_at <= now()
       {stage_dialable}
       {replied_guard}
       {suppression}
       {legal_window}
       {preference_window}
     -- CARRY-OVERS FIRST. first_dialed_at IS NULL sorts last, so anything
     -- already started - callbacks, L3 follow-ups, retries - goes ahead of
     -- new leads. Nothing is ever dropped: what is not reached stays queued
     -- and comes up again tomorrow.
     ORDER BY (l.first_dialed_at IS NULL), l.next_attempt_at
       FOR UPDATE OF l SKIP LOCKED
     LIMIT %(limit)s
"""


def _build_select():
    return SELECT_DUE.format(
        queue=QUEUE_MEMBERSHIP,
        campaign=CAMPAIGN_MEMBERSHIP,
        stage_dialable=STAGE_DIALABLE,
        replied_guard=REPLIED_GUARD,
        suppression=SUPPRESSION_JOIN,
        legal_window=windows.LEGAL_WINDOW,
        preference_window=windows.PREFERENCE_WINDOW,
    )


def _audit(conn, lead_id, phone, outcome, detail=None, call_id=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO dial_audit (lead_id, phone_e164, call_id, outcome, detail)
               VALUES (%s, %s, %s, %s, %s)""",
            (lead_id, phone, call_id, outcome, detail))


def select_and_claim(cfg, date=None, limit: int = 10):
    """
    Select due leads and claim them in the SAME transaction, with
    FOR UPDATE SKIP LOCKED so a second worker - or a re-entrant cron - cannot
    hand the same lead to two dialers.

    Rollovers sort first: a lead due yesterday that never got dialed has
    waited longest.
    """
    # Checked HERE too, so a stopped campaign produces no candidates at all
    # rather than claims that are refused one by one.
    campaign = campaigns.running()
    if not campaign:
        return []
    claimed = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_build_select(),
                        {'limit': limit, 'campaign_id': campaign['campaign_id']})
            for r in cur.fetchall():
                cur.execute(
                    """UPDATE leads SET status = 'dialing', last_called_at = now(),
                                        updated_at = now()
                        WHERE lead_id = %s""", (r['lead_id'],))
                claimed.append({**r, 'prior_status': r['status'],
                                'campaign': campaign})
    return claimed


def _revert(lead, outcome, detail):
    """Refusal: audit it and put the lead back. A silent refusal is how you
    spend an hour asking why nothing dialed."""
    with db.get_conn() as conn:
        _audit(conn, lead['lead_id'], lead['phone_e164'], outcome, detail)
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE leads SET status = %s, updated_at = now() WHERE lead_id = %s',
                (lead['prior_status'], lead['lead_id']))


_REFUSAL_OUTCOMES = (
    ('suppressed', 'refused_suppressed'),
    ('no campaign is running', 'refused_paused'),
    ('daily cap', 'refused_cap'),
    ('allowlist', 'refused_allowlist'),
    ('DIAL_MODE', 'refused_allowlist'),
)


def dial_one(cfg, lead, use_web: bool = False):
    """Guard, then dial. Returns the call_id, or None if refused/failed."""
    phone = lead['phone_e164']
    try:
        with db.get_conn() as conn:
            # RE-READ the campaign. The row on the claimed lead is a snapshot
            # from selection time, so a pause during an in-flight batch was
            # invisible to the guard below - which is the one case the
            # in-transaction re-check exists for. Read the lead's OWN
            # campaign, not whatever is running now: if A was stopped and B
            # started, this lead must not be dialed under B's config.
            with conn.cursor() as cur:
                cur.execute('SELECT campaign_id FROM leads WHERE lead_id = %s',
                            (lead['lead_id'],))
                row = cur.fetchone()
            cid = row and row['campaign_id']
            # No fallback to running(): "whatever is running now" is how a
            # lead claimed under A ends up dialed under B.
            campaign = campaigns.get(cid) if cid else None
            # All four re-checked in the SAME transaction as the dial. The
            # gap between claiming a batch and dialing it is real: a call can
            # end with "remove me", or someone can hit pause, while an earlier
            # batch is still in flight.
            assert_not_suppressed(conn, phone)
            assert_campaign_running(campaign)
            assert_under_daily_cap(conn, lead, campaign, cfg.OPERATOR_TIMEZONE)
            assert_dialable(phone, cfg)

            # Every {{variable}} any prompt uses, built in one place. An
            # unsupplied variable is left in the prompt text verbatim.
            dynamic = retell.dynamic_vars(lead)
            if use_web:
                resp = retell.create_web_call(cfg, lead, dynamic)
            else:
                resp = retell.create_phone_call(cfg, phone, lead, dynamic)
            call_id = getattr(resp, 'call_id', None)

            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE leads
                          SET last_call_id = %s, attempts = attempts + 1,
                              -- stamped ONCE: this is how the daily cap counts
                              -- new leads without a retry consuming budget
                              first_dialed_at = COALESCE(first_dialed_at, now()),
                              updated_at = now()
                        WHERE lead_id = %s""", (call_id, lead['lead_id']))
            _audit(conn, lead['lead_id'], phone, 'dialed', None, call_id)
            return call_id

    except DialRefused as exc:
        msg = str(exc)
        outcome = next((o for k, o in _REFUSAL_OUTCOMES if k in msg),
                       'refused_other')
        _revert(lead, outcome, msg)
        print(f'[dialer] REFUSED {phone}: {exc}', flush=True)
        return None

    except Exception as exc:
        with db.get_conn() as conn:
            _audit(conn, lead['lead_id'], phone, 'api_error',
                   f'{type(exc).__name__}: {exc}'[:2000])
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE leads SET status = 'no_answer', last_outcome = 'api_error',
                            next_attempt_at = now() + interval '4 hours',
                            updated_at = now()
                        WHERE lead_id = %s""", (lead['lead_id'],))
        print(f'[dialer] API ERROR {phone}: {exc}', flush=True)
        return None


def run_once(cfg=None, limit: int = None) -> int:
    """
    One tick. Dials at most `max_concurrent` leads - operator-set, default 1.

    This limit is the real spacing control. The worker's interval governs how
    often a tick happens; THIS governs how many calls a tick can fire. With a
    batch limit of 10 a single tick could place ten calls 0.2s apart, which
    defeats any interval however wide.
    """
    cfg = cfg or load_config()
    if limit is None:
        camp = campaigns.running()
        limit = camp['max_concurrent'] if camp else 1
    placed = 0
    for lead in select_and_claim(cfg, limit=limit):
        if dial_one(cfg, lead) is not None:
            placed += 1
        if placed < limit:
            time.sleep(0.2)
    return placed
