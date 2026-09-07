"""
Draft follow-up emails. GENERATED, NEVER SENT.

Nothing in this repo sends email to a lead. A draft is written when we capture
a name and a confirmed email; a person opens the lead, reads it, edits if
needed, sends it themselves, then clicks "mark as sent" - which is the
EXISTING L2 -> L3 transition (stages.mark_emailed), not a second one.

THE OPENER IS THE WHOLE POINT. It references the call. Without that this is
cold outreach and the phone call was wasted.

"pointed me your way", never "gave me your contact info" - the second reads
like the receptionist handed over data and can get her in trouble.

The sample is LINKED, never attached. Law-firm mail security strips
attachments from unknown senders, and a click is a signal an attachment can
never give us.
"""

import datetime

from api import db, settings as settings_mod

SAMPLE_URL = 'https://counselorai.io/#letter'

SUBJECT_WITH_NAME = 'Following up — spoke with your front desk'
# The template reads "this morning". The body derives morning/afternoon from
# the actual call time, so the SUBJECT must use the same phrase or the two
# contradict each other in the same email.
SUBJECT_WITHOUT = 'Quick follow-up from {when}'

BODY = """Hi {first_name},

{opener}

We built CounselorAI for PI firms — it drafts the full demand package
from the case records, with citations verified against a closed
library of published opinions. Most firms spend six to eight hours on
one; this takes about thirty minutes.

First one's free on a real file, no card. If it's not better than what
you'd have sent, you've lost fifteen minutes.

Sample demand, redacted: {sample_url}

Worth a look?

{sender_name}
CounselorAI

Reply "unsubscribe" and I'll take you off the list.
{company_line}
"""

OPENER_WITH_NAME = ('{gatekeeper} at your front desk pointed me your way — '
                    'she said you\'re the one who handles demand letters.')
OPENER_WITHOUT = ('I called your office {when} and your front desk pointed me '
                  'your way on demand letters.')


def first_name(full: str) -> str:
    """'Sara Whitfield' -> 'Sara'. Falls back to 'there' rather than emitting
    an empty greeting."""
    part = (full or '').strip().split()
    return part[0] if part else 'there'


def _when_phrase(called_at, tz):
    """
    'this morning' or 'this afternoon', in the LEAD's local time.

    The template says "this morning". Keeping that literal would be wrong on
    any afternoon call, and a receptionist reading it would notice - so the
    word is derived from when the call actually happened. Change it here if
    you would rather it always read "this morning".
    """
    if not called_at:
        return 'this morning'
    try:
        from zoneinfo import ZoneInfo
        local = called_at.astimezone(ZoneInfo(tz))
    except Exception:
        return 'this morning'
    return 'this morning' if local.hour < 12 else 'this afternoon'


def build(lead) -> dict:
    """Returns {to_email, subject, body, variant}. Pure - no database write."""
    st = settings_mod.all_settings()
    sender_name = st.get('sender_name') or 'Sean'
    company_line = st.get('sender_company_line') or 'CounselorAI LLC'

    gatekeeper = (lead.get('gatekeeper_name') or '').strip()
    if gatekeeper:
        opener = OPENER_WITH_NAME.format(gatekeeper=gatekeeper)
        subject, variant = SUBJECT_WITH_NAME, 'with_name'
    else:
        when = _when_phrase(lead.get('last_called_at'),
                            lead.get('timezone') or 'UTC')
        opener = OPENER_WITHOUT.format(when=when)
        subject, variant = SUBJECT_WITHOUT.format(when=when), 'without_name'

    body = BODY.format(
        first_name=first_name(lead.get('dm_name')),
        opener=opener, sample_url=SAMPLE_URL,
        sender_name=sender_name, company_line=company_line)
    return {'to_email': (lead.get('dm_email') or '').strip(),
            'subject': subject, 'body': body, 'variant': variant}


def generate_for(lead_id, force: bool = False):
    """
    Write a draft for a lead that has a name and a CONFIRMED email.

    Does not overwrite an edited draft unless forced - a human's edits must
    not be clobbered by a later call re-triggering generation.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lead_id,))
            lead = cur.fetchone()
            if lead is None:
                return None
            if not lead['dm_email'] or lead['dm_email_confirmed'] is not True:
                return None
            cur.execute('SELECT edited_at FROM email_drafts WHERE lead_id = %s',
                        (lead_id,))
            existing = cur.fetchone()
            if existing and existing['edited_at'] and not force:
                return None      # a person has edited this - leave it alone

            d = build(dict(lead))
            cur.execute(
                """INSERT INTO email_drafts (lead_id, to_email, subject, body, variant)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (lead_id) DO UPDATE
                     SET to_email=EXCLUDED.to_email, subject=EXCLUDED.subject,
                         body=EXCLUDED.body, variant=EXCLUDED.variant,
                         generated_at=now()""",
                (lead_id, d['to_email'], d['subject'], d['body'], d['variant']))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s,'note','draft email generated (NOT sent)',%s)""",
                (lead_id, f"variant={d['variant']}"))
            return d


def save_edit(lead_id, subject: str, body: str, edited_by: str = 'operator'):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE email_drafts SET subject=%s, body=%s,
                          edited_at=now(), edited_by=%s
                    WHERE lead_id=%s RETURNING lead_id""",
                (subject, body, edited_by, lead_id))
            return cur.fetchone() is not None


def get(lead_id):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM email_drafts WHERE lead_id = %s', (lead_id,))
            return cur.fetchone()


def generate_pending(limit: int = 25) -> dict:
    """
    Write drafts for any lead that has a name and a confirmed email but no
    draft yet.

    A queue drain, NOT a call inside the webhook transaction: at the moment
    the capture is written the row is not committed, so generating there would
    read stale data or couple two concerns. NEVER RAISES - a draft failure
    must not break the call flow, exactly like scoring.
    """
    made, failed = 0, 0
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT l.lead_id FROM leads l
                        LEFT JOIN email_drafts d ON d.lead_id = l.lead_id
                       WHERE d.lead_id IS NULL
                         AND l.dm_email IS NOT NULL
                         AND l.dm_email_confirmed IS TRUE
                       ORDER BY l.updated_at LIMIT %s""", (limit,))
                ids = [r['lead_id'] for r in cur.fetchall()]
    except Exception as exc:
        print(f'[drafts] scan failed: {exc}', flush=True)
        return {'drafted': 0, 'failed': 1}

    for lid in ids:
        try:
            if generate_for(lid):
                made += 1
        except Exception as exc:
            failed += 1
            print(f'[drafts] {lid} failed: {type(exc).__name__}: {exc}', flush=True)
    return {'drafted': made, 'failed': failed}
