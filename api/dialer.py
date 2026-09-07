"""
The dialer loop.

Order is: select -> claim -> guard -> dial. Each step exists because the one
before it cannot do its job.

CALLING WINDOW: not enforced here yet. The 8:00-20:30 client-local window and
the per-weekday preference are PHASE 2 (they are that phase's headline item,
with their own break pass). In phase 1 the only thing that can be dialed is a
number on DIAL_ALLOWLIST - an explicit list of numbers we own - so there is no
path to a stranger's phone at a bad hour. This comment is here so nobody reads
the absence as an oversight.

SUPPRESSION is enforced twice, deliberately - see api/guards.py.
"""

import time

from api import db, retell
from api.config import load_config
from api.guards import DialRefused, assert_dialable, assert_not_suppressed

# Isolated so the break pass can remove exactly this and nothing else.
# A suppressed number must never be a CANDIDATE, not merely never dialed.
SUPPRESSION_JOIN = (
    'AND NOT EXISTS (SELECT 1 FROM suppression s '
    'WHERE s.phone_e164 = l.phone_e164)'
)

SELECT_DUE = """
    SELECT l.lead_id, l.phone_e164, l.status, l.stage, l.attempts,
           l.dm_name, l.callback_person
      FROM leads l
     WHERE l.pool_status = 'active'
       AND l.status IN ('new', 'callback', 'no_answer')
       AND l.next_attempt_at <= now()
       {suppression}
     ORDER BY l.next_attempt_at
       FOR UPDATE OF l SKIP LOCKED
     LIMIT %s
"""


def _audit(conn, lead_id, phone, outcome, detail=None, call_id=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO dial_audit (lead_id, phone_e164, call_id, outcome, detail)
               VALUES (%s, %s, %s, %s, %s)""",
            (lead_id, phone, call_id, outcome, detail),
        )


def select_and_claim(limit: int = 10):
    """
    Select due leads and claim them in the SAME transaction, with
    FOR UPDATE SKIP LOCKED so a second worker - or a re-entrant cron - cannot
    hand the same lead to two dialers.

    Returns the claimed rows, each carrying prior_status so a later refusal
    can put the lead back where it was.
    """
    claimed = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                SELECT_DUE.format(suppression=SUPPRESSION_JOIN), (limit,)
            )
            rows = cur.fetchall()
            for r in rows:
                cur.execute(
                    """UPDATE leads
                          SET status = 'dialing', last_called_at = now(),
                              updated_at = now()
                        WHERE lead_id = %s""",
                    (r['lead_id'],),
                )
                claimed.append({**r, 'prior_status': r['status']})
    return claimed


def _revert(lead, outcome, detail):
    """Refusal: audit it and put the lead back. Silent refusals are how you
    spend an hour asking why nothing dialed."""
    with db.get_conn() as conn:
        _audit(conn, lead['lead_id'], lead['phone_e164'], outcome, detail)
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE leads SET status = %s, updated_at = now() WHERE lead_id = %s',
                (lead['prior_status'], lead['lead_id']),
            )


def dial_one(cfg, lead, use_web: bool = False):
    """
    Guard, then dial. Returns the call_id, or None if the call was refused
    or the API failed.
    """
    phone = lead['phone_e164']
    try:
        with db.get_conn() as conn:
            # RE-CHECK immediately before the dial, in the same transaction.
            # A call can end with "remove me" and write a suppression row
            # while this batch is still in flight.
            assert_not_suppressed(conn, phone)
            assert_dialable(phone, cfg)

            dynamic = {
                'lead_id': str(lead['lead_id']),
                'callback_person': lead.get('callback_person') or '',
                'dm_name': lead.get('dm_name') or '',
            }
            if use_web:
                resp = retell.create_web_call(cfg, lead['lead_id'], dynamic)
            else:
                resp = retell.create_phone_call(
                    cfg, phone, lead['lead_id'], dynamic
                )
            call_id = getattr(resp, 'call_id', None)

            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE leads
                          SET last_call_id = %s, attempts = attempts + 1,
                              updated_at = now()
                        WHERE lead_id = %s""",
                    (call_id, lead['lead_id']),
                )
            _audit(conn, lead['lead_id'], phone, 'dialed', None, call_id)
            return call_id

    except DialRefused as exc:
        outcome = ('refused_suppressed' if 'suppressed' in str(exc)
                   else 'refused_allowlist')
        _revert(lead, outcome, str(exc))
        print(f'[dialer] REFUSED {phone}: {exc}', flush=True)
        return None

    except Exception as exc:
        # API error: audit, then back off rather than hammering Retell.
        with db.get_conn() as conn:
            _audit(conn, lead['lead_id'], phone, 'api_error',
                   f'{type(exc).__name__}: {exc}'[:2000])
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE leads
                          SET status = 'no_answer',
                              next_attempt_at = now() + interval '4 hours',
                              updated_at = now()
                        WHERE lead_id = %s""",
                    (lead['lead_id'],),
                )
        print(f'[dialer] API ERROR {phone}: {exc}', flush=True)
        return None


def run_once(cfg=None, limit: int = 10) -> int:
    cfg = cfg or load_config()
    placed = 0
    for lead in select_and_claim(limit):
        if dial_one(cfg, lead) is not None:
            placed += 1
        # Retell is not rate limited at this volume, but pacing keeps a burst
        # of claims from becoming a burst of simultaneous calls.
        time.sleep(0.2)
    return placed
