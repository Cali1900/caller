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

from api import campaigns as campaigns_mod, db

PLACEHOLDERS = ('first_name', 'gatekeeper_name', 'company', 'time_of_day',
                'sender_name', 'footer')


def first_name(full: str) -> str:
    """'Sara Whitfield' -> 'Sara'. Falls back to 'there' rather than emitting
    an empty greeting."""
    part = (full or '').strip().split()
    return part[0] if part else 'there'


def _when_phrase(called_at, tz):
    """
    'this morning' or 'this afternoon', in the LEAD's local time.

    Derived rather than literal: an email sent after an afternoon call that
    says "this morning" is something a receptionist notices.
    """
    if not called_at:
        return 'this morning'
    try:
        from zoneinfo import ZoneInfo
        local = called_at.astimezone(ZoneInfo(tz))
    except Exception:
        return 'this morning'
    return 'this morning' if local.hour < 12 else 'this afternoon'


def render(template: str, values: dict) -> str:
    """
    Substitute {{placeholder}}. UNKNOWN PLACEHOLDERS ARE LEFT ALONE rather
    than blanked - a stray {{foo}} visible in the preview is a typo the
    operator can see and fix, whereas silently deleting it produces a
    sentence with a hole in it that nobody notices until it is sent.
    """
    out = template or ''
    for k in PLACEHOLDERS:
        out = out.replace('{{' + k + '}}', str(values.get(k, '') or ''))
    return out


def values_for(lead, campaign) -> dict:
    campaign = campaign or {}
    return {
        'first_name': first_name(lead.get('dm_name')),
        'gatekeeper_name': (lead.get('gatekeeper_name') or '').strip(),
        'company': (lead.get('company') or 'the firm').strip(),
        'time_of_day': _when_phrase(lead.get('last_called_at'),
                                    lead.get('timezone') or 'UTC'),
        'sender_name': campaign.get('sender_name') or 'Sean',
        'footer': campaign.get('sender_company_line') or 'CounselorAI LLC',
    }


def build(lead, campaign=None) -> dict:
    """
    Returns {to_email, subject, body, variant}. Pure - no database write.

    THE COPY BELONGS TO THE CAMPAIGN. Two campaigns can run different copy;
    that is most of the reason to have a second campaign.
    """
    from api import campaigns as campaigns_mod
    if campaign is None:
        if lead.get('campaign_id'):
            campaign = campaigns_mod.get(lead['campaign_id'])
        campaign = campaign or campaigns_mod.running() or {}

    vals = values_for(lead, campaign)
    with_name = bool(vals['gatekeeper_name'])
    sub_key = 'subject_with_name' if with_name else 'subject_without'
    body_key = 'body_with_name' if with_name else 'body_without'
    # A lead with no campaign still gets the default copy. An EMPTY body is a
    # blank email that could reach a person; the columns are NOT NULL and the
    # save route refuses empty text, so the only way to land here is having no
    # campaign at all, and the default is the right answer for that.
    subject_tpl = campaign.get(sub_key) or campaigns_mod.DEFAULT_TEMPLATE[sub_key]
    body_tpl = campaign.get(body_key) or campaigns_mod.DEFAULT_TEMPLATE[body_key]

    return {'to_email': (lead.get('dm_email') or '').strip(),
            'subject': render(subject_tpl, vals),
            'body': render(body_tpl, vals),
            'variant': 'with_name' if with_name else 'without_name'}


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
            # Rewrite the sample link to a tracked one. Done HERE, on the
            # stored draft, because this is the text that gets copied into a
            # mail client - the preview shows the same thing for the same
            # reason. An unset CLICK_BASE_URL leaves the plain link alone.
            from api import clicks as _clicks
            from api.config import load_config as _load
            try:
                d['body'] = _clicks.rewrite(d['body'], _load().CLICK_BASE_URL,
                                            lead_id)
            except Exception as exc:
                print(f'[drafts] click rewrite skipped: {exc}', flush=True)
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


def retarget(lead_id, new_email: str):
    """
    Point an UNSENT draft at a corrected address.

    The draft stores to_email at generation time. Correcting the contact email
    on the lead used to leave that copy behind, so the address you had already
    fixed was still the one sitting in the draft - and the whole point of this
    screen is that you copy what you see into your mail client.

    A SENT draft is left alone. Its to_email is a record of where the mail
    actually went; rewriting it would falsify history. leads.emailed_at is the
    sent marker.

    Returns 'updated', 'already_sent', or None when there is nothing to do.
    """
    new_email = (new_email or '').strip()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT l.emailed_at, d.to_email
                             FROM leads l
                             JOIN email_drafts d ON d.lead_id = l.lead_id
                            WHERE l.lead_id = %s""", (lead_id,))
            row = cur.fetchone()
            if row is None:
                return None                     # no draft to retarget
            if row['to_email'] == new_email:
                return None
            if row['emailed_at'] is not None:
                # Deliberately visible: the draft and the contact now disagree,
                # and that is a fact about the past, not a bug to paper over.
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s,'note','contact email changed AFTER sending',%s)""",
                    (lead_id, f"draft still records {row['to_email']} - "
                              f"that is where the mail went"))
                return 'already_sent'
            cur.execute('UPDATE email_drafts SET to_email=%s WHERE lead_id=%s',
                        (new_email, lead_id))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s,'note','draft retargeted to the corrected email',%s)""",
                (lead_id, f"{row['to_email']} -> {new_email or '(none)'}"))
            return 'updated'


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


SAMPLE_LEAD = {
    'lead_id': None, 'dm_name': 'Sara Whitfield', 'company': 'Whitfield Injury Law',
    'gatekeeper_name': 'Denise', 'dm_email': 'sara@whitfieldinjury.example',
    'timezone': 'America/Los_Angeles', 'last_called_at': None,
}


def preview_lead(campaign_id=None):
    """
    A REAL lead to render the template against, so the preview shows the copy
    that will actually go out rather than lorem ipsum.

    Prefers a lead that has been called (its time_of_day is real) and has a
    gatekeeper name, so both variants have something to show. Falls back to
    any lead on the campaign, then to any lead at all, and only then to a
    clearly-labelled sample - a preview against invented data must announce
    itself, otherwise it is indistinguishable from a preview against a lead.
    """
    where, params = '', {}
    if campaign_id:
        where, params = 'WHERE campaign_id = %(cid)s', {'cid': campaign_id}
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for clause in (where, ''):
                cur.execute(f"""
                    SELECT lead_id, dm_name, company, gatekeeper_name, dm_email,
                           timezone, last_called_at
                      FROM leads {clause}
                     ORDER BY (dm_name IS NOT NULL AND dm_name <> '') DESC,
                              (gatekeeper_name IS NOT NULL
                               AND gatekeeper_name <> '') DESC,
                              (last_called_at IS NOT NULL) DESC,
                              last_called_at DESC NULLS LAST
                     LIMIT 1""", params if clause else {})
                row = cur.fetchone()
                if row:
                    return dict(row), True
    return dict(SAMPLE_LEAD), False


def preview(campaign, lead=None, overrides=None):
    """
    Render both variants of a campaign's copy.

    `overrides` lets the editor preview UNSAVED text - what you are looking at
    is what is in the boxes, not what is in the database.
    """
    camp = dict(campaign or {})
    camp.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if lead is None:
        lead, _real = preview_lead(camp.get('campaign_id'))

    with_name = dict(lead, gatekeeper_name=lead.get('gatekeeper_name') or 'Denise')
    without = dict(lead, gatekeeper_name='')
    out = {'with_name': build(with_name, camp),
           'without_name': build(without, camp)}
    # Same rewrite as the stored draft: the preview must show what will
    # actually be sent, tracked link included.
    from api import clicks as _clicks
    from api.config import load_config as _load
    try:
        base = _load().CLICK_BASE_URL
        if base and lead.get('lead_id'):
            for v in out.values():
                v['body'] = _clicks.rewrite(v['body'], base, lead['lead_id'])
    except Exception:
        pass
    return out
