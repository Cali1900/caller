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
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from api import (campaigns, db, digest as digest_mod, drafts as drafts_mod,
                 prompts as prompts_mod, settings as settings_mod, stages,
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


def _lead_query(q, status, stage, needs_you, limit, offset):
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
    sql = f"""
        SELECT l.*,
               (SELECT count(*) FROM calls c WHERE c.lead_id = l.lead_id) AS call_count,
               sc.agent_score  AS last_agent,
               sc.outcome_score AS last_outcome_score,
               sc.their_words
          FROM leads l
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
               needs_you: str = '', page: int = 1, msg: str = ''):
    """THE LANDING PAGE. Where each firm stands, not a numbers dashboard."""
    cfg = _cfg()
    page = max(1, page)
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
        sql, params, where, cparams = _lead_query(
            q, status, stage, needs_you, PAGE, (page - 1) * PAGE)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(f"SELECT count(*) AS n FROM leads l WHERE {' AND '.join(where)}",
                        cparams)
            total = cur.fetchone()['n']
    for r in rows:
        r['agent_class'] = _score_class(r.get('last_agent'))
        r['outcome_class'] = _score_class(r.get('last_outcome_score'))
    qs = urllib.parse.urlencode(
        {k: v for k, v in
         (('q', q), ('status', status), ('stage', stage), ('needs_you', needs_you))
         if v})
    return templates.TemplateResponse(request, 'leads.html', {
        'hdr': hdr, 'leads': rows, 'q': q, 'status': status,
        'stage': stage, 'needs_you': needs_you, 'statuses': STATUSES,
        'stages': STAGES, 'total': total, 'qs': qs, 'page': page, 'msg': msg,
        'dialing_on': settings_mod.get('dialing_enabled'),
        'pages': max(1, (total + PAGE - 1) // PAGE)})


@router.post('/leads/queue')
async def leads_queue(request: Request):
    """
    "Add to campaign" - puts the selected leads in the STANDING QUEUE.

    This does NOT start dialing. Dialing is one explicit switch that defaults
    to off; queueing a thousand leads with the switch off places zero calls.
    """
    form = await request.form()
    ids = form.getlist('lead_id')
    action = form.get('action', 'add')
    if not ids:
        return RedirectResponse('/?msg=nothing+selected', status_code=303)
    target = 'active' if action == 'add' else 'pool'
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads SET pool_status = %s, updated_at = now()
                    WHERE lead_id = ANY(%s::uuid[]) AND pool_status <> 'done'""",
                (target, ids))
            n = cur.rowcount
    verb = 'added to' if action == 'add' else 'removed from'
    on = settings_mod.get('dialing_enabled')
    msg = (f'{n} lead(s) {verb} the queue.'
           + ('' if on else ' Dialing is OFF - nothing will be called until you turn it on.'))
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
    return templates.TemplateResponse(request, 'lead.html', {
        'hdr': hdr, 'lead': lead, 'timeline': timeline,
        'suppressed': suppressed, 'local_time': local, 'saved': saved,
        'draft': draft})


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
            if old != (dm_email.strip() or None):
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s,'note','contact edited by hand',%s)""",
                    (lead_id, f'email {old or "(none)"} -> {dm_email.strip() or "(none)"}'))
    return RedirectResponse(f'/leads/{lead_id}?saved=Saved.', status_code=303)


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


@router.get('/campaign', response_class=HTMLResponse)
def campaign_page(request: Request, msg: str = ''):
    cfg = _cfg()
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                     count(*) FILTER (WHERE pool_status='pool')            AS pool,
                     count(*) FILTER (WHERE pool_status='active')          AS queued,
                     count(*) FILTER (WHERE pool_status='active'
                                        AND first_dialed_at IS NULL)       AS queued_new,
                     count(*) FILTER (WHERE pool_status='active'
                                        AND first_dialed_at IS NOT NULL
                                        AND status IN ('callback','no_answer','new','queued'))
                                                                           AS queued_carry,
                     count(*) FILTER (WHERE first_dialed_at IS NOT NULL
                                        AND (first_dialed_at AT TIME ZONE %s)::date
                                            = (now() AT TIME ZONE %s)::date) AS new_today,
                     (SELECT count(*) FROM suppression)                    AS suppressed
                   FROM leads""", (cfg.OPERATOR_TIMEZONE, cfg.OPERATOR_TIMEZONE))
            queue = cur.fetchone()
            cur.execute('SELECT * FROM dialing_windows ORDER BY dow')
            windows = cur.fetchall()
    prompt_rows = {'L1': prompts_mod.listing(cfg, 'L1'),
                   'L3': prompts_mod.listing(cfg, 'L3')}
    st = settings_mod.all_settings(force=True)
    avg = (st['dial_interval_min'] + st['dial_interval_max']) / 2.0
    per_hour = round(3600.0 / avg * st['max_concurrent'], 1) if avg else 0
    remaining = max(0, st['daily_cap'] - (queue['new_today'] or 0))
    hours_left = round(remaining / per_hour, 1) if per_hour else 0
    return templates.TemplateResponse(request, 'campaign.html', {
        'hdr': hdr, 'queue': queue, 'msg': msg, 'settings': st,
        'per_hour': per_hour, 'remaining': remaining, 'hours_left': hours_left,
        'windows': windows, 'days': DAYS, 'prompt_rows': prompt_rows})


@router.post('/campaign/dialing')
def campaign_dialing(on: str = Form(...)):
    """
    THE SWITCH. Replaces the old start button, and defaults to off.

    One place, deliberately: adding leads must never be able to start dialing.
    """
    settings_mod.set_many({'dialing_enabled': on}, updated_by='operator')
    live = settings_mod.all_settings(force=True)['dialing_enabled']
    msg = 'DIALING IS ON' if live else 'dialing is paused'
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/campaign/cap')
def campaign_cap(daily_cap: str = Form(...)):
    r = settings_mod.set_many({'daily_cap': daily_cap})
    msg = (f"cap set to {r['set']['daily_cap']} new leads/day" if r['ok']
           else 'REJECTED: ' + '; '.join(f'{k}: {e}' for k, e in r['errors'].items()))
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/campaign/spacing')
def campaign_spacing(max_concurrent: str = Form(...),
                     dial_interval_min: str = Form(...),
                     dial_interval_max: str = Form(...)):
    """
    Spacing is the OPERATOR's setting, changed here rather than in env,
    because an env change needs a container recreate.
    """
    r = settings_mod.set_many({'max_concurrent': max_concurrent,
                               'dial_interval_min': dial_interval_min,
                               'dial_interval_max': dial_interval_max})
    if r['ok']:
        v = r['set']
        msg = (f"spacing: {v.get('max_concurrent','-')} at a time, "
               f"{v.get('dial_interval_min','-')}-{v.get('dial_interval_max','-')}s apart "
               f"(takes effect on the next tick, no restart)")
    else:
        msg = 'REJECTED: ' + '; '.join(f'{k}: {e}' for k, e in r['errors'].items())
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg[:300])}',
                            status_code=303)


@router.post('/campaign/prompt')
def campaign_prompt(stage: str = Form('L1'), agent_version: str = Form(...)):
    """
    Choose which prompt version is LIVE.

    This exists because v8 became live as a SIDE EFFECT - agent.update()
    applies to whatever draft is sitting in the dashboard. Editing a draft in
    Retell must not change what dials; only this does.
    """
    key = 'agent_l1_version' if stage == 'L1' else 'agent_l3_version'
    r = settings_mod.set_many({key: agent_version})
    msg = (f'{stage} now runs v{r["set"][key]} on new calls' if r['ok']
           else 'REJECTED: ' + '; '.join(f'{k}: {e}' for k, e in r['errors'].items()))
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg[:300])}#prompts',
                            status_code=303)


@router.post('/campaign/prompt/note')
def campaign_prompt_note(stage: str = Form('L1'), agent_version: int = Form(...),
                         note: str = Form('')):
    cfg = _cfg()
    agent_id = cfg.AGENT_L1 if stage == 'L1' else cfg.AGENT_L3
    ok = prompts_mod.set_note(agent_id, agent_version, note.strip())
    return RedirectResponse(
        f'/campaign?msg={urllib.parse.quote(("note saved" if ok else "no such version"))}#prompts',
        status_code=303)


@router.post('/campaign/prompt/sync')
def campaign_prompt_sync():
    cfg = _cfg()
    a = prompts_mod.sync_versions(cfg, 'L1')
    b = prompts_mod.sync_versions(cfg, 'L3')
    msg = f"synced {a['versions']} L1 and {b['versions']} L3 versions from Retell"
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg)}#prompts',
                            status_code=303)


@router.post('/campaign/sender')
def campaign_sender(sender_email: str = Form(...), sender_name: str = Form(...),
                    sender_company_line: str = Form(...)):
    """
    From-address is a FIELD, not an env var: counselorai.io now,
    demandcounselor.com once warm, and that switch should not be a deploy.
    """
    r = settings_mod.set_many({'sender_email': sender_email,
                               'sender_name': sender_name,
                               'sender_company_line': sender_company_line})
    msg = (f"sender set to {r['set'].get('sender_email','')}" if r['ok']
           else 'REJECTED: ' + '; '.join(f'{k}: {e}' for k, e in r['errors'].items()))
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg[:300])}',
                            status_code=303)


@router.post('/campaign/windows')
async def campaign_windows(request: Request):
    """
    Per-weekday calling window, in the CALLED PARTY's local time.

    These can only ever NARROW the TCPA window - the legal 08:00-20:30 check is
    ANDed in separately and no value here can widen past it.
    """
    form = await request.form()
    changed = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for dow in range(7):
                enabled = form.get(f'enabled_{dow}') == 'on'
                start = (form.get(f'start_{dow}') or '09:00').strip()
                end = (form.get(f'end_{dow}') or '17:00').strip()
                cur.execute(
                    """UPDATE dialing_windows
                          SET enabled=%s, start_time=%s::time, end_time=%s::time
                        WHERE dow=%s AND (enabled, start_time, end_time)
                              IS DISTINCT FROM (%s, %s::time, %s::time)""",
                    (enabled, start, end, dow, enabled, start, end))
                if cur.rowcount:
                    changed.append(DAYS[dow])
    msg = ('windows updated: ' + ', '.join(changed)) if changed else 'windows unchanged'
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(msg[:300])}',
                            status_code=303)


@router.post('/campaign/action')
def campaign_action(do: str = Form(...), cap: int = Form(200)):
    cfg = _cfg()
    if do == 'cap':
        campaigns.ensure(cfg)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'UPDATE campaigns SET daily_cap=%s WHERE campaign_date=%s',
                    (cap, campaigns.campaign_date(cfg)))
        msg = f'cap set to {cap}'
    elif do in ('enroll', 'start', 'pause', 'resume', 'rollover'):
        result = getattr(campaigns, do)(cfg)
        msg = f'{do}: {result}'
    else:
        msg = f'unknown action {do!r}'
    return RedirectResponse(f'/campaign?msg={urllib.parse.quote(str(msg)[:300])}',
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
