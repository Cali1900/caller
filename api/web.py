"""
The CRM. Server-rendered HTML, no build step, no login.

NO LOGIN IS DELIBERATE AND SAFE ONLY BECAUSE OF THE BINDING. caller-api listens
on 127.0.0.1 and the nginx vhost proxies exactly one path (/webhooks/retell)
and 404s everything else, so every route in this module is reachable only
through an SSH tunnel:

    ssh -L 4100:localhost:4100 root@ssh.demand.legaltoolsgpt.com

If anyone ever publishes this port, these pages become an unauthenticated
lead database with a DNC button on the internet. The binding IS the auth.

Templates are Jinja2 with autoescaping ON. Company names and call transcripts
are text other people produced; concatenating them into HTML would execute
whatever a receptionist happened to say.
"""

import csv
import datetime
import io
import urllib.parse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from api import (campaigns, db, digest as digest_mod, drafts as drafts_mod,
                 senders as senders_mod,
                 prompts as prompts_mod, stages,
                 upload as upload_mod)
from api.config import load_config

router = APIRouter()
templates = Jinja2Templates(directory='api/templates')

STATUSES = ['new', 'queued', 'dialing', 'completed', 'callback', 'no_answer',
            'email_path', 'demo_pending', 'dnc', 'max_attempts', 'failed',
            'human_review', 'paused']
STAGES = ['L1', 'L2', 'L3', 'L4', 'won', 'lost']
DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday',
        'Friday', 'Saturday']
PAGE = 100


def _cfg():
    return load_config()


def _score_class(v):
    if v is None:
        return ''
    return 's-hi' if v >= 8 else ('s-mid' if v >= 5 else 's-lo')


def _header(conn, cfg):
    """The strip on every page: today's numbers and what needs a person."""
    date = campaigns.campaign_date(cfg)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS dialed,
                      round(avg(s.agent_score)::numeric,1)   AS avg_agent,
                      round(avg(s.outcome_score)::numeric,1) AS avg_outcome
                 FROM calls c LEFT JOIN call_scores s ON s.call_id = c.call_id
                WHERE c.created_at >= %s::date AND c.created_at < (%s::date + 1)""",
            (date, date))
        h = dict(cur.fetchone())
        cur.execute(_NEEDS_YOU_COUNT)
        h['needs_you'] = cur.fetchone()['n']
    return h


# A lead "needs you" when a PERSON has to act: an unconfirmed email that would
# burn a sending domain, a call the scorer flagged, or a verbal demo yes with
# no invite sent (phase 6 - the row already counts).
_NEEDS_YOU_PREDICATE = """
    (l.status IN ('human_review', 'demo_pending')
     OR (l.dm_email IS NOT NULL AND l.dm_email_confirmed IS NOT TRUE)
     OR EXISTS (SELECT 1 FROM call_scores s2
                 JOIN calls c2 ON c2.call_id = s2.call_id
                WHERE c2.lead_id = l.lead_id AND s2.needs_human))
"""
_NEEDS_YOU_COUNT = f"SELECT count(*) AS n FROM leads l WHERE {_NEEDS_YOU_PREDICATE}"


# THE EMAIL STATE OF A LEAD, in one expression so the list and the filter can
# never disagree about what "draft ready" means.
#
# Precedence is the order things happen in: a reply outranks a send, a send
# outranks a draft. NO OPEN TRACKING - Apple Mail Privacy Protection pre-loads
# pixels, so an "opened" count on a list of lawyers is noise. Replies only.
#
# 'replied' is INERT: nothing writes leads.replied_at yet. The column is here
# so the state is visible the day detection lands, not a claim that it works.
_EMAIL_STATE = """
    CASE WHEN l.replied_at IS NOT NULL           THEN 'replied'
         WHEN l.emailed_at IS NOT NULL           THEN 'sent'
         WHEN d.lead_id IS NOT NULL              THEN 'draft_ready'
         ELSE 'none' END
"""

EMAIL_STATES = ('draft_ready', 'sent', 'replied', 'none')


def _lead_query(q, status, stage, needs_you, limit, offset, email_state=''):
    where, params = ["1=1"], []
    if q:
        where.append("(l.company ILIKE %s OR l.phone_e164 ILIKE %s "
                     "OR l.dm_email ILIKE %s OR l.dm_name ILIKE %s "
                     "OR l.city ILIKE %s OR l.external_ref ILIKE %s)")
        params += [f'%{q}%'] * 6
    if status:
        where.append("l.status = %s"); params.append(status)
    if stage:
        where.append("l.stage = %s"); params.append(stage)
    if needs_you:
        where.append(_NEEDS_YOU_PREDICATE)
    if email_state in EMAIL_STATES:
        where.append(f'({_EMAIL_STATE.strip()}) = %s'); params.append(email_state)
    sql = f"""
        SELECT l.*,
               ({_EMAIL_STATE.strip()}) AS email_state,
               (SELECT count(*) FROM calls c WHERE c.lead_id = l.lead_id) AS call_count,
               sc.agent_score  AS last_agent,
               sc.outcome_score AS last_outcome_score,
               sc.their_words
          FROM leads l
          LEFT JOIN email_drafts d ON d.lead_id = l.lead_id
          LEFT JOIN LATERAL (
              SELECT s.agent_score, s.outcome_score, s.their_words
                FROM call_scores s JOIN calls c ON c.call_id = s.call_id
               WHERE c.lead_id = l.lead_id
               ORDER BY c.created_at DESC LIMIT 1) sc ON true
         WHERE {' AND '.join(where)}
         ORDER BY (l.last_called_at IS NULL), l.last_called_at DESC, l.created_at DESC
         LIMIT %s OFFSET %s"""
    return sql, params + [limit, offset], where, params


@router.get('/', response_class=HTMLResponse)
def leads_list(request: Request, q: str = '', status: str = '', stage: str = '',
               needs_you: str = '', email_state: str = '', page: int = 1,
               msg: str = ''):
    """THE LANDING PAGE. Where each firm stands, not a numbers dashboard."""
    cfg = _cfg()
    page = max(1, page)
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
        sql, params, where, cparams = _lead_query(
            q, status, stage, needs_you, PAGE, (page - 1) * PAGE, email_state)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            # The same draft join as the list query - the email-state predicate
            # references it, and a count that cannot see `d` would 500 or, worse,
            # silently disagree with the rows on screen.
            cur.execute(
                f"""SELECT count(*) AS n FROM leads l
                     LEFT JOIN email_drafts d ON d.lead_id = l.lead_id
                    WHERE {' AND '.join(where)}""",
                cparams)
            total = cur.fetchone()['n']
    for r in rows:
        r['agent_class'] = _score_class(r.get('last_agent'))
        r['outcome_class'] = _score_class(r.get('last_outcome_score'))
    qs = urllib.parse.urlencode(
        {k: v for k, v in
         (('q', q), ('status', status), ('stage', stage),
          ('needs_you', needs_you), ('email_state', email_state))
         if v})
    return templates.TemplateResponse(request, 'leads.html', {
        'hdr': hdr, 'leads': rows, 'q': q, 'status': status,
        'stage': stage, 'needs_you': needs_you, 'statuses': STATUSES,
        'email_state': email_state,
        'stages': STAGES, 'total': total, 'qs': qs, 'page': page, 'msg': msg,
        'campaigns': campaigns.list_all(), 'running': campaigns.running(),
        'pages': max(1, (total + PAGE - 1) // PAGE)})


@router.post('/leads/queue')
async def leads_queue(request: Request):
    """
    "Add to campaign" - ASSIGNS the leads to a campaign and queues them.

    Assignment and queued-ness are separate concerns but this is the one
    action an operator thinks of as a single step. It still never dials:
    leads on a campaign that is not running sit idle.
    """
    form = await request.form()
    ids = form.getlist('lead_id')
    action = form.get('action', 'add')
    campaign_id = form.get('campaign_id') or None
    if not ids:
        return RedirectResponse('/?msg=nothing+selected', status_code=303)

    if action == 'remove':
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE leads SET pool_status='pool', updated_at=now()
                                WHERE lead_id = ANY(%s::uuid[])
                                  AND pool_status <> 'done'""", (ids,))
                n = cur.rowcount
        return RedirectResponse(
            f'/?msg={urllib.parse.quote(f"{n} lead(s) removed from the queue")}',
            status_code=303)

    if not campaign_id:
        return RedirectResponse('/?msg=pick+a+campaign+first', status_code=303)
    camp = campaigns.get(campaign_id)
    if camp is None:
        return RedirectResponse('/?msg=no+such+campaign', status_code=303)

    campaigns.assign(ids, campaign_id)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads SET pool_status='active', updated_at=now()
                            WHERE lead_id = ANY(%s::uuid[])
                              AND pool_status <> 'done'""", (ids,))
            n = cur.rowcount
    tail = ('' if camp['is_running']
            else f' {camp["name"]} is NOT running, so they will not dial yet.')
    msg = f'{n} lead(s) added to {camp["name"]}.{tail}'
    return RedirectResponse(f'/?msg={urllib.parse.quote(msg)}', status_code=303)


@router.get('/leads/{lead_id}', response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: str, saved: str = ''):
    cfg = _cfg()
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lead_id,))
            lead = cur.fetchone()
            if lead is None:
                return HTMLResponse('<p>no such lead</p>', status_code=404)
            cur.execute('SELECT * FROM suppression WHERE phone_e164 = %s',
                        (lead['phone_e164'],))
            suppressed = cur.fetchone()
            cur.execute('SELECT * FROM email_drafts WHERE lead_id = %s', (lead_id,))
            draft = cur.fetchone()

            # Every call, with both scores and what they actually said.
            cur.execute(
                """SELECT c.*, s.agent_score, s.outcome_score, s.agent_deductions,
                          s.what_happened, s.where_it_broke, s.their_words,
                          s.needs_human, a.last_error AS score_error
                     FROM calls c
                     LEFT JOIN call_scores s   ON s.call_id = c.call_id
                     LEFT JOIN score_attempts a ON a.call_id = c.call_id
                    WHERE c.lead_id = %s ORDER BY c.created_at DESC""", (lead_id,))
            calls = cur.fetchall()
            cur.execute(
                """SELECT * FROM activity WHERE lead_id = %s
                    ORDER BY created_at DESC LIMIT 200""", (lead_id,))
            acts = cur.fetchall()

    timeline = []
    for c in calls:
        lat = (c['latency'] or {}).get('e2e', {}) if isinstance(c['latency'], dict) else {}
        timeline.append({
            'at': c['created_at'], 'kind': 'call', 'stage': c['stage'],
            'summary': f"call {c['call_status'] or ''}".strip(),
            'call_id': c['call_id'], 'transcript': c['transcript'],
            'duration': round((c['duration_ms'] or 0) / 1000),
            'disconnection_reason': c['disconnection_reason'],
            'cost_cents': float(c['cost_cents']) if c['cost_cents'] else None,
            'latency_p50': round(lat['p50']) if lat.get('p50') else None,
            'agent_score': c['agent_score'], 'outcome_score': c['outcome_score'],
            'agent_class': _score_class(c['agent_score']),
            'outcome_class': _score_class(c['outcome_score']),
            'agent_deductions': c['agent_deductions'],
            'what_happened': c['what_happened'],
            'where_it_broke': c['where_it_broke'], 'their_words': c['their_words'],
            'needs_human': c['needs_human'], 'score_error': c['score_error'],
        })
    for a in acts:
        if a['kind'] == 'call':
            continue          # already rendered above, with its scores
        timeline.append({'at': a['created_at'], 'kind': a['kind'],
                         'stage': a['stage'], 'summary': a['summary'],
                         'detail': a['detail']})
    timeline.sort(key=lambda t: t['at'], reverse=True)

    try:
        local = datetime.datetime.now(ZoneInfo(lead['timezone'])).strftime('%a %H:%M')
    except Exception:
        local = '?'
    # The sender identity is the CAMPAIGN's, and it is shown on the draft
    # because you copy this into a mail client by hand - if the From: is not
    # in front of you, you cannot check you are sending as the right person.
    camp = campaigns.get(lead['campaign_id']) if lead.get('campaign_id') else None
    return templates.TemplateResponse(request, 'lead.html', {
        'hdr': hdr, 'lead': lead, 'timeline': timeline,
        'suppressed': suppressed, 'local_time': local, 'saved': saved,
        'draft': draft, 'campaign': camp})


@router.post('/leads/{lead_id}/edit')
def lead_edit(lead_id: str, dm_name: str = Form(''), dm_title: str = Form(''),
              dm_email: str = Form(''), dm_email_confirmed: str = Form(''),
              notes: str = Form('')):
    """Fix a wrong email. Every edit lands on the timeline."""
    confirmed = {'true': True, 'false': False}.get(dm_email_confirmed, None)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT dm_email FROM leads WHERE lead_id = %s', (lead_id,))
            prev = cur.fetchone()
            cur.execute(
                """UPDATE leads SET dm_name = NULLIF(%s,''), dm_title = NULLIF(%s,''),
                          dm_email = NULLIF(%s,''), dm_email_confirmed = %s,
                          notes = NULLIF(%s,''), updated_at = now()
                    WHERE lead_id = %s""",
                (dm_name.strip(), dm_title.strip(), dm_email.strip(),
                 confirmed, notes.strip(), lead_id))
            old = (prev or {}).get('dm_email')
            changed = old != (dm_email.strip() or None)
            if changed:
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s,'note','contact edited by hand',%s)""",
                    (lead_id, f'email {old or "(none)"} -> {dm_email.strip() or "(none)"}'))

    msg = 'Saved.'
    if changed:
        # The draft holds its own copy of the address. Leaving it stale means
        # copying an address you already corrected into your mail client.
        outcome = drafts_mod.retarget(lead_id, dm_email)
        if outcome == 'updated':
            msg = 'Saved. Draft now addressed to the corrected email.'
        elif outcome == 'already_sent':
            msg = ('Saved. The draft was ALREADY SENT, so its To: still shows '
                   'the old address - that is where the mail went.')
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/leads/{lead_id}/draft/save')
def draft_save(lead_id: str, subject: str = Form(...), body: str = Form(...)):
    ok = drafts_mod.save_edit(lead_id, subject, body)
    msg = 'Draft saved.' if ok else 'No draft to save.'
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}#draft',
                            status_code=303)


@router.post('/leads/{lead_id}/draft/regenerate')
def draft_regenerate(lead_id: str):
    d = drafts_mod.generate_for(lead_id, force=True)
    msg = 'Draft regenerated from the template.' if d else 'Could not generate (needs a confirmed email).'
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}#draft',
                            status_code=303)


@router.post('/leads/{lead_id}/emailed')
def lead_emailed(lead_id: str, emailed_by: str = Form('operator')):
    """
    "I emailed them" - L2 -> L3, follow-up due in 3 days.

    This handler does NOT contain the transition. stages.mark_emailed() does,
    because the coming sequencer from demandcounselor.com will call the same
    function with emailed_by='auto:<domain>'. Two callers, one transition.
    """
    row = stages.mark_emailed(lead_id, emailed_by=emailed_by)
    if row is None:
        msg = 'Not at L2 - nothing changed. (Already emailed, or no confirmed email yet.)'
    else:
        msg = f"Marked emailed. Follow-up call queued for {row['next_attempt_at']:%Y-%m-%d}."
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/leads/{lead_id}/dnc')
def lead_dnc(lead_id: str):
    """
    Suppression row AND status=dnc in ONE transaction. Not a follow-up job.
    The suppression list is the highest-liability object in the system.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO suppression (phone_e164, reason, source)
                   SELECT phone_e164, 'requested', 'crm' FROM leads WHERE lead_id = %s
                   ON CONFLICT (phone_e164) DO NOTHING""", (lead_id,))
            cur.execute(
                "UPDATE leads SET status='dnc', updated_at=now() WHERE lead_id=%s",
                (lead_id,))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary)
                   VALUES (%s,'dnc','marked DNC from the CRM - suppressed')""",
                (lead_id,))
    return RedirectResponse(f'/leads/{lead_id}?saved=Marked DNC and suppressed.',
                            status_code=303)


@router.get('/campaigns', response_class=HTMLResponse)
def campaigns_list(request: Request, msg: str = '', confirm: str = ''):
    """Saved campaigns, which one is running, and create new."""
    cfg = _cfg()
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
    rows = campaigns.list_all()
    pending = campaigns.get(confirm) if confirm else None
    return templates.TemplateResponse(request, 'campaigns.html', {
        'hdr': hdr, 'campaigns': rows, 'msg': msg,
        'running': campaigns.running(), 'pending': pending})


@router.post('/campaigns/new')
def campaigns_new(name: str = Form(...), template_from: str = Form(''),
                  notes: str = Form('')):
    try:
        row = campaigns.create(name, template_from=template_from or None,
                               notes=notes)
    except Exception as exc:
        return RedirectResponse(
            f'/campaigns?msg={urllib.parse.quote("could not create: " + str(exc)[:150])}',
            status_code=303)
    return RedirectResponse(f'/campaign/{row["campaign_id"]}'
                            f'?msg={urllib.parse.quote(row["name"] + " created (stopped)")}',
                            status_code=303)


@router.get('/campaign', response_class=HTMLResponse)
def campaign_redirect():
    """/campaign means 'the one that is running', else the list."""
    run = campaigns.running()
    if run:
        return RedirectResponse(f'/campaign/{run["campaign_id"]}', status_code=303)
    return RedirectResponse('/campaigns', status_code=303)


@router.get('/campaign/{campaign_id}', response_class=HTMLResponse)
def campaign_page(request: Request, campaign_id: str, msg: str = ''):
    cfg = _cfg()
    camp = campaigns.get(campaign_id)
    if camp is None:
        return HTMLResponse('<p>no such campaign</p>', status_code=404)
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
    q = campaigns.queue_stats(cfg, campaign_id)
    avg = (camp['dial_interval_min'] + camp['dial_interval_max']) / 2.0
    per_hour = round(3600.0 / avg * camp['max_concurrent'], 1) if avg else 0
    remaining = max(0, camp['daily_cap'] - (q['new_today'] or 0))
    prompts_mod.sync_if_stale(cfg, 'L1')
    pv_lead, pv_real = drafts_mod.preview_lead(campaign_id)
    _sender_opts = senders_mod.options(cfg, camp['sender_email'])
    return templates.TemplateResponse(request, 'campaign.html', {
        'hdr': hdr, 'c': camp, 'queue': q, 'msg': msg,
        'per_hour': per_hour, 'remaining': remaining,
        'hours_left': round(remaining / per_hour, 1) if per_hour else 0,
        'windows': campaigns.windows(campaign_id), 'days': DAYS,
        'prompt_rows': {'L1': prompts_mod.listing(cfg, 'L1'),
                        'L3': prompts_mod.listing(cfg, 'L3')},
        'sender_options': _sender_opts[0], 'sender_error': _sender_opts[1],
        'placeholders': drafts_mod.PLACEHOLDERS,
        'preview': drafts_mod.preview(camp, lead=pv_lead),
        'preview_lead': pv_lead, 'preview_real': pv_real,
        'running': campaigns.running()})


@router.get('/prompts', response_class=HTMLResponse)
def prompts_page(request: Request, msg: str = ''):
    """Prompt versions with their notes. A version is not a campaign - it is
    something a campaign points at, so this is a reference list, not a picker."""
    cfg = _cfg()
    # Pulled on load, not on a button. You should not have to click sync to see
    # a version you published an hour ago; that is how the list came to sit
    # eight versions behind Retell.
    sync_err = prompts_mod.sync_if_stale(cfg, 'L1')
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
    return templates.TemplateResponse(request, 'prompts.html', {
        'hdr': hdr, 'msg': msg, 'sync_err': sync_err,
        'prompt_rows': {'L1': prompts_mod.listing(cfg, 'L1'),
                        'L3': prompts_mod.listing(cfg, 'L3')},
        'campaigns': campaigns.list_all()})


@router.post('/prompts/note')
def prompts_note(stage: str = Form('L1'), agent_version: int = Form(...),
                 note: str = Form('')):
    cfg = _cfg()
    agent_id = cfg.AGENT_L1 if stage == 'L1' else cfg.AGENT_L3
    ok = prompts_mod.set_note(agent_id, agent_version, note.strip())
    return RedirectResponse(
        f'/prompts?msg={urllib.parse.quote("note saved" if ok else "no such version")}',
        status_code=303)


@router.post('/prompts/sync')
def prompts_sync():
    cfg = _cfg()
    # The button now forces a FULL re-fetch; the incremental sync already ran
    # on page load. This is for "Retell changed something under a version".
    a = prompts_mod.sync_versions(cfg, 'L1', force=True)
    b = prompts_mod.sync_versions(cfg, 'L3', force=True)
    msg = (f"re-fetched {a['fetched']} of {a['versions']} L1 and "
           f"{b['fetched']} of {b['versions']} L3 versions from Retell")
    return RedirectResponse(f'/prompts?msg={urllib.parse.quote(msg)}', status_code=303)


@router.post('/campaign/{campaign_id}/save')
def campaign_save(campaign_id: str, name: str = Form(...), notes: str = Form(''),
                  agent_l1_version: int = Form(...), agent_l3_version: int = Form(...),
                  sender_email: str = Form(...), sender_name: str = Form(...),
                  sender_company_line: str = Form(...), daily_cap: int = Form(...),
                  max_concurrent: int = Form(...), dial_interval_min: int = Form(...),
                  dial_interval_max: int = Form(...)):
    if dial_interval_min > dial_interval_max:
        return RedirectResponse(
            f'/campaign/{campaign_id}?msg={urllib.parse.quote("REJECTED: gap min cannot exceed gap max")}',
            status_code=303)
    # The sender is a choice from a list. A typed address that Brevo has never
    # heard of is the config-that-cannot-send this replaced a text field to stop.
    if not senders_mod.is_allowed(_cfg(), sender_email):
        return RedirectResponse(
            f'/campaign/{campaign_id}?msg={urllib.parse.quote(f"REJECTED: {sender_email} is not an offered or verified sender")}',
            status_code=303)
    try:
        campaigns.update(campaign_id, name=name, notes=notes,
                         agent_l1_version=agent_l1_version,
                         agent_l3_version=agent_l3_version,
                         sender_email=sender_email, sender_name=sender_name,
                         sender_company_line=sender_company_line,
                         daily_cap=daily_cap, max_concurrent=max_concurrent,
                         dial_interval_min=dial_interval_min,
                         dial_interval_max=dial_interval_max)
        msg = 'saved'
    except Exception as exc:
        msg = f'REJECTED: {str(exc)[:150]}'
    return RedirectResponse(f'/campaign/{campaign_id}?msg={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/campaign/{campaign_id}/email')
def campaign_email_save(campaign_id: str,
                        subject_with_name: str = Form(...),
                        subject_without: str = Form(...),
                        body_with_name: str = Form(...),
                        body_without: str = Form(...)):
    """The copy is a property of the campaign, saved on its own form so a copy
    edit never has to pass the cap and spacing validation."""
    fields = {'subject_with_name': subject_with_name.strip(),
              'subject_without': subject_without.strip(),
              'body_with_name': body_with_name, 'body_without': body_without}
    empty = [k for k, v in fields.items() if not v.strip()]
    if empty:
        msg = 'REJECTED: empty ' + ', '.join(empty)
    else:
        campaigns.update(campaign_id, **fields)
        msg = 'copy saved'
    return RedirectResponse(f'/campaign/{campaign_id}?msg={urllib.parse.quote(msg)}#copy',
                            status_code=303)


@router.post('/campaign/{campaign_id}/email/preview')
async def campaign_email_preview(campaign_id: str, request: Request):
    """
    Renders the text CURRENTLY IN THE BOXES against a real lead.

    Server-side on purpose: the preview must go through the same render() the
    draft generator uses, or it is a second implementation that can disagree
    with what actually gets sent.
    """
    camp = campaigns.get(campaign_id)
    if camp is None:
        return JSONResponse({'error': 'no such campaign'}, status_code=404)
    form = await request.form()
    overrides = {f: form.get(f) for f in campaigns.TEMPLATE_FIELDS
                 if form.get(f) is not None}
    lead, real = drafts_mod.preview_lead(campaign_id)
    out = drafts_mod.preview(camp, lead=lead, overrides=overrides)
    return JSONResponse({
        'lead': {'name': lead.get('dm_name'), 'company': lead.get('company'),
                 'real': real},
        'with_name': out['with_name'], 'without_name': out['without_name']})


@router.post('/campaign/{campaign_id}/windows')
async def campaign_windows_save(campaign_id: str, request: Request):
    form = await request.form()
    rows = {d: (form.get(f'enabled_{d}') == 'on',
                (form.get(f'start_{d}') or '09:00').strip(),
                (form.get(f'end_{d}') or '17:00').strip()) for d in range(7)}
    changed = campaigns.set_windows(campaign_id, rows)
    msg = ('window updated: ' + ', '.join(DAYS[d] for d in changed)) if changed \
          else 'window unchanged'
    return RedirectResponse(f'/campaign/{campaign_id}?msg={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/campaign/{campaign_id}/start')
def campaign_start(campaign_id: str, stop_running: str = Form('')):
    """
    Start a campaign.

    NEVER A SILENT HANDOVER. If another is running this bounces to a
    confirmation naming both, and only comes back here with stop_running set.
    """
    try:
        row = campaigns.start(campaign_id, stop_running=(stop_running == 'yes'))
    except campaigns.CampaignConflict:
        return RedirectResponse(f'/campaigns?confirm={campaign_id}', status_code=303)
    return RedirectResponse(
        f'/campaign/{campaign_id}?msg={urllib.parse.quote(row["name"] + " is RUNNING")}',
        status_code=303)


@router.post('/campaign/{campaign_id}/stop')
def campaign_stop(campaign_id: str):
    row = campaigns.stop(campaign_id)
    name = row['name'] if row else 'campaign'
    return RedirectResponse(
        f'/campaign/{campaign_id}?msg={urllib.parse.quote(name + " stopped - nothing dials")}',
        status_code=303)


@router.post('/upload-form')
async def upload_form(file: UploadFile = File(...)):
    text = (await file.read()).decode('utf-8', errors='replace')
    r = upload_mod.upload(text)
    msg = (f"{r['inserted']} added to the pool, {r['duplicates']} duplicates, "
           f"{r['rejected']} rejected")
    if r['rejects']:
        msg += ' (' + '; '.join(f"line {x['line']}: {x['reason']}"
                                for x in r['rejects'][:3]) + ')'
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg[:300])}',
                            status_code=303)


@router.get('/today', response_class=HTMLResponse)
def today_page(request: Request, sent: str = ''):
    cfg = _cfg()
    d = digest_mod.build(cfg)
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT l.lead_id, l.company,
                       CASE WHEN l.status = 'demo_pending' THEN 'demo pending'
                            WHEN l.status = 'human_review' THEN 'human review'
                            WHEN l.dm_email IS NOT NULL AND l.dm_email_confirmed IS NOT TRUE
                                 THEN 'email unconfirmed'
                            ELSE 'flagged call' END AS why,
                       (SELECT s.their_words FROM call_scores s
                          JOIN calls c ON c.call_id = s.call_id
                         WHERE c.lead_id = l.lead_id ORDER BY c.created_at DESC
                         LIMIT 1) AS their_words
                  FROM leads l WHERE {_NEEDS_YOU_PREDICATE}
                 ORDER BY l.updated_at DESC LIMIT 50""")
            needs = cur.fetchall()
    return templates.TemplateResponse(request, 'today.html', {
        'hdr': hdr, 'date': d['date'], 'body': d['body'],
        'needs_you': needs, 'sent': sent, 'digest_hour': 18})


@router.post('/today/send')
def today_send():
    r = digest_mod.send(_cfg(), force=True)
    return RedirectResponse(f'/today?sent={urllib.parse.quote(str(r)[:200])}',
                            status_code=303)


@router.get('/export.csv')
def export_csv(q: str = '', status: str = '', stage: str = '', needs_you: str = ''):
    sql, params, _, _ = _lead_query(q, status, stage, needs_you, 100000, 0)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    cols = ['company', 'phone_e164', 'timezone', 'city', 'state', 'stage',
            'status', 'dm_name', 'dm_title', 'dm_email', 'dm_email_confirmed',
            'attempts', 'callback_count', 'last_called_at', 'next_attempt_at',
            'last_agent', 'last_outcome_score', 'their_words', 'notes',
            'external_ref', 'lead_id']
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c) for c in cols])
    buf.seek(0)
    stamp = datetime.date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]), media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename=caller-leads-{stamp}.csv'})
