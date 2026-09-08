"""
THE SENDER — the only thing in this repo that puts campaign mail on the wire.

Everything dangerous is deliberately somewhere else:
  * WHETHER a lead may be auto-sent      -> api/autosend.py (the gate)
  * WHETHER an address may be mailed AT ALL -> guards.assert_emailable
  * WHAT the email says                  -> api/drafts.py (the campaign's copy)

This module's whole job is: re-check everything in one transaction, refuse
loudly, audit either way, and send exactly once.

RE-CHECKED AT SEND TIME, not trusted from selection. The same lesson as
dial_one: the gap between deciding and doing is real. A lead can reply, be
marked DNC, or have its campaign paused between the tick that selected it and
the moment the mail would go out.

SENDS EXACTLY ONCE. mark_emailed() is write-once, so the send is claimed by
stamping emailed_at BEFORE the API call. A crash after the stamp loses one
email; a crash after the call without the stamp would send a SECOND one to a
real person. Those are not equivalent failures.
"""

import datetime

from api import (autosend, campaigns, db, drafts, guards, mail, stages)
from api.guards import EmailRefused


def _audit(cur, lead_id, to_email, outcome, detail=''):
    cur.execute(
        """INSERT INTO email_audit (lead_id, to_email, outcome, detail)
           VALUES (%s,%s,%s,%s)""", (lead_id, to_email, outcome, detail[:500]))


def due(cfg, limit: int = 25):
    """
    Leads whose campaign is on AUTO and whose delay has elapsed.

    Selection is deliberately loose - it finds candidates. Every actual
    exclusion is re-checked in send_one, inside the transaction that sends.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.lead_id
                  FROM leads l
                  JOIN campaign_configs c ON c.campaign_id = l.campaign_id
                  JOIN email_drafts d     ON d.lead_id = l.lead_id
                 WHERE c.email_1_mode = 'auto'
                   AND l.stage = 'L2'
                   AND l.emailed_at IS NULL
                   AND l.replied_at IS NULL
                   AND l.last_called_at IS NOT NULL
                   AND l.last_called_at
                       < now() - (c.email_1_delay_minutes || ' minutes')::interval
                 ORDER BY l.last_called_at
                 LIMIT %s""", (limit,))
            return [r['lead_id'] for r in cur.fetchall()]


def send_one(cfg, lead_id) -> dict:
    """
    {'sent': bool, 'detail': str}. NEVER RAISES.

    Refusing is a normal outcome and is audited. An exception is not - it is
    also audited, and it is still a refusal.
    """
    to_email = None
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lead_id,))
                lead = cur.fetchone()
                if lead is None:
                    return {'sent': False, 'detail': 'no such lead'}
                lead = dict(lead)
                to_email = (lead.get('dm_email') or '').strip()

                camp = (campaigns.get(lead['campaign_id'])
                        if lead.get('campaign_id') else None)

                # 1. THE GATE, re-run here rather than trusted from selection.
                decision = autosend.eligibility(lead, camp)
                if not decision['ok']:
                    reason = '; '.join(decision['reasons'])
                    _audit(cur, lead_id, to_email, 'refused_ineligible', reason)
                    return {'sent': False, 'detail': reason}

                # 2. THE DEV GUARD. Last line, and it fails closed.
                try:
                    guards.assert_emailable(to_email, cfg)
                except EmailRefused as exc:
                    _audit(cur, lead_id, to_email, 'refused_allowlist', str(exc))
                    return {'sent': False, 'detail': str(exc)}

                draft = drafts.get(lead_id)
                if not draft:
                    _audit(cur, lead_id, to_email, 'refused_no_draft',
                           'no draft to send')
                    return {'sent': False, 'detail': 'no draft'}

        # 3. CLAIM THE SEND BEFORE MAKING IT. mark_emailed is write-once, so
        #    this is what makes a double send impossible. Losing one email to a
        #    crash beats sending a second one to a real person.
        claimed = stages.mark_emailed(lead_id, emailed_by=f'auto:{cfg.SENDER_DOMAIN}')
        if claimed is None:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    _audit(cur, lead_id, to_email, 'refused_already_sent',
                           'emailed_at was already set')
            return {'sent': False, 'detail': 'already sent'}

        result = mail.send(cfg, to_email, draft['subject'], draft['body'],
                           sender_email=camp.get('sender_email'),
                           sender_name=camp.get('sender_name'))

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if result.get('ok'):
                    _audit(cur, lead_id, to_email, 'sent',
                           f"as {camp.get('sender_email')}")
                else:
                    # The stamp STAYS. We do not know whether Brevo accepted it,
                    # and un-stamping would let the next tick send again.
                    _audit(cur, lead_id, to_email, 'send_failed',
                           str(result.get('detail'))[:400])
                    cur.execute(
                        """INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'note','auto-send FAILED - needs a person',%s)""",
                        (lead_id, str(result.get('detail'))[:400]))
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


def run_once(cfg, limit: int = 25) -> dict:
    sent = refused = 0
    for lead_id in due(cfg, limit):
        r = send_one(cfg, lead_id)
        sent += bool(r['sent'])
        refused += not r['sent']
    return {'sent': sent, 'refused': refused}
