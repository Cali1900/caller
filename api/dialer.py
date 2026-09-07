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
    assert_campaign_running     pause, for a lead already claimed
    assert_under_cap            the daily cap
"""

import time

from api import campaigns, db, retell, settings as settings_mod, windows
from api.config import load_config
from api.guards import (DialRefused, assert_campaign_running, assert_dialable,
                        assert_not_suppressed, assert_under_cap)

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

# Nothing dials until a campaign exists, has been STARTed, and is not paused.
# Uploading leads does not dial. Enrolling them does not dial. Only START.
CAMPAIGN_JOIN = """
    JOIN campaign_leads cl
      ON cl.lead_id = l.lead_id AND cl.campaign_date = %(date)s
    JOIN campaigns c
      ON c.campaign_date = cl.campaign_date
     AND c.started_at IS NOT NULL
     AND NOT c.paused
"""

SELECT_DUE = """
    SELECT l.lead_id, l.phone_e164, l.status, l.stage, l.attempts,
           l.company, l.dm_name, l.dm_title, l.dm_email, l.emailed_at,
           l.callback_person, cl.source
      FROM leads l
      {campaign}
     WHERE cl.dialed_at IS NULL
       AND l.pool_status = 'active'
       AND l.status IN ('new', 'callback', 'no_answer', 'queued')
       AND l.next_attempt_at <= now()
       {stage_dialable}
       {replied_guard}
       {suppression}
       {legal_window}
       {preference_window}
     ORDER BY array_position(ARRAY['rollover','callback','retry','fresh'],
                             cl.source),
              l.next_attempt_at
       FOR UPDATE OF l SKIP LOCKED
     LIMIT %(limit)s
"""


def _build_select():
    return SELECT_DUE.format(
        campaign=CAMPAIGN_JOIN,
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
    date = date or campaigns.campaign_date(cfg)
    claimed = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_build_select(), {'date': date, 'limit': limit})
            for r in cur.fetchall():
                cur.execute(
                    """UPDATE leads SET status = 'dialing', last_called_at = now(),
                                        updated_at = now()
                        WHERE lead_id = %s""", (r['lead_id'],))
                claimed.append({**r, 'prior_status': r['status'],
                                'campaign_date': date})
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
    ('cap', 'refused_cap'),
    ('paused', 'refused_paused'),
    ('not started', 'refused_not_started'),
    ('allowlist', 'refused_allowlist'),
    ('DIAL_MODE', 'refused_allowlist'),
)


def dial_one(cfg, lead, use_web: bool = False):
    """Guard, then dial. Returns the call_id, or None if refused/failed."""
    phone = lead['phone_e164']
    date = lead.get('campaign_date') or campaigns.campaign_date(cfg)
    try:
        with db.get_conn() as conn:
            # All four re-checked in the SAME transaction as the dial. The
            # gap between claiming a batch and dialing it is real: a call can
            # end with "remove me", or someone can hit pause, while an earlier
            # batch is still in flight.
            assert_not_suppressed(conn, phone)
            assert_campaign_running(conn, date)
            assert_under_cap(conn, date, lead.get('source', 'fresh'))
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
                    """UPDATE leads SET last_call_id = %s, attempts = attempts + 1,
                                        updated_at = now()
                        WHERE lead_id = %s""", (call_id, lead['lead_id']))
                cur.execute(
                    """UPDATE campaign_leads SET dialed_at = now()
                        WHERE campaign_date = %s AND lead_id = %s""",
                    (date, lead['lead_id']))
                cur.execute(
                    """UPDATE campaigns SET dialed_count = dialed_count + 1
                        WHERE campaign_date = %s""", (date,))
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
        limit = settings_mod.get('max_concurrent')
    placed = 0
    for lead in select_and_claim(cfg, limit=limit):
        if dial_one(cfg, lead) is not None:
            placed += 1
        if placed < limit:
            time.sleep(0.2)
    return placed
