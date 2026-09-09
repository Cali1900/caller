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
import re
import urllib.parse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from api import (archive as _archive_mod,
                 drain as _drain, why as _why,
                 retry_ladder as _rl,
                 campaigns, clicks as clicks_mod, db,
                 forecast as forecast_mod, funnel as funnel_mod,
                 digest as digest_mod, drafts as drafts_mod,
                 senders as senders_mod,
                 prompts as prompts_mod, stages,
                 upload as upload_mod)
from api.config import load_config

router = APIRouter()
templates = Jinja2Templates(directory='api/templates')


def _ago(when):
    """'5m ago'. A relative time answers "is this fresh?" without arithmetic."""
    if not when:
        return ''
    import datetime as _dt
    secs = (_dt.datetime.now(_dt.UTC) - when).total_seconds()
    if secs < 60:
        return 'just now'
    for unit, n in (('m', 60), ('h', 3600), ('d', 86400)):
        if secs < n * 60 or unit == 'd':
            return f'{int(secs // n)}{unit} ago'
    return when.strftime('%b %-d')


templates.env.filters['ago'] = _ago

STATUSES = ['new', 'queued', 'dialing', 'completed', 'callback', 'no_answer',
            'email_path', 'demo_pending', 'dnc', 'max_attempts', 'failed',
            'human_review', 'paused',
            'emailed', 'engaged', 'demo_booked', 'won', 'lost',
            'lost_no_response', 'bad_email', 'archived']
# L1 and L2 are the only stages a lead can hold - the DB constraint
# agrees. won/lost live on status, which is the one home for them.
STAGES = ['L1', 'L2']
DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday',
        'Friday', 'Saturday']
PAGE = 100
# Selectable page sizes. Bounded on purpose: an unbounded "all" is a Select All
# that queues leads nobody has looked at.
PAGE_SIZES = (100, 200, 500)


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
# 'replied' is LIVE now: the "I got a reply" checkbox on lead detail writes
# leads.replied_at through stages.record_reply(). Sean reads every reply at
# this volume, so a person ticking a box is the detector - and automatic
# ingest, when built, becomes a second writer to the same field.
_EMAIL_STATE = """
    CASE WHEN l.replied_at IS NOT NULL           THEN 'replied'
         WHEN ck.clicks > 0                      THEN 'clicked'
         WHEN l.emailed_at IS NOT NULL           THEN 'sent'
         WHEN d.lead_id IS NOT NULL              THEN 'draft_ready'
         ELSE 'none' END
"""

EMAIL_STATES = ('draft_ready', 'sent', 'clicked', 'replied', 'none')


# Sortable columns. The VALUE is the expression; direction is applied
# separately so one key serves both directions.
#
# NULLS LAST everywhere, in BOTH directions, on purpose: "never called" and
# "she did not answer" are absences, not zeros, and sorting them among the
# real values says they are. An unknown belongs at the bottom whichever way
# the arrow points.
SORTS = {
    'firm':      'l.company',
    'city':      'l.city',
    'state':     'l.state',
    'stage':     'l.stage',
    'status':    'l.status',
    'calls':     'call_count',
    'emails':    'em.email_count',
    'agent':     'sc.agent_score',
    'outcome':   'sc.outcome_score',
    'volume':    'l.demands_per_month',
    'last_call': 'l.last_called_at',
    'last_email': 'em.last_email_at',
    'recent':    'l.last_called_at',
}
DEFAULT_SORT, DEFAULT_DIR = 'recent', 'desc'


def _order_by(sort, direction):
    col = SORTS.get(sort) or SORTS[DEFAULT_SORT]
    d = 'ASC' if direction == 'asc' else 'DESC'
    # l.company is the tiebreak so paging is STABLE - without a deterministic
    # tiebreak a lead can appear on two pages or on none.
    return f'{col} {d} NULLS LAST, l.company ASC, l.lead_id ASC'


def _score_range(where, params, field, lo, hi):
    """agent < 7, outcome >= 8. Applied to the LATEST score for the lead."""
    if lo not in (None, ''):
        where.append(f'{field} >= %s'); params.append(int(lo))
    if hi not in (None, ''):
        where.append(f'{field} <= %s'); params.append(int(hi))


_LEAD_JOINS = """
      FROM leads l
      LEFT JOIN email_drafts d ON d.lead_id = l.lead_id
      LEFT JOIN campaign_configs cc ON cc.campaign_id = l.campaign_id
      LEFT JOIN LATERAL (
          SELECT count(*) AS clicks, min(minutes_since_sent) AS first_minutes
            FROM email_clicks ec WHERE ec.lead_id = l.lead_id) ck ON true
      LEFT JOIN LATERAL (
          SELECT count(*) AS email_count, max(created_at) AS last_email_at
            FROM email_audit ea
           WHERE ea.lead_id = l.lead_id
             AND ea.outcome IN ('sent', 'sent_manual')) em ON true
      LEFT JOIN LATERAL (
          SELECT count(*) AS call_count,
                 -- "reached a human": every call whose disconnection reason
                 -- is not one of the never-connected ones. The list is
                 -- drain.NO_CONNECT_REASONS, passed in rather than restated,
                 -- so a new reason cannot mean connected here and not there.
                 count(*) FILTER (
                     WHERE coalesce(c.disconnection_reason,'') <> ''
                       AND NOT (c.disconnection_reason = ANY(""" + _drain.NO_CONNECT_SQL + """))
                 ) AS human_calls
            FROM calls c WHERE c.lead_id = l.lead_id) ca ON true
      LEFT JOIN LATERAL (
          SELECT t.tags FROM (
              SELECT array_agg(tag ORDER BY tag) AS tags
                FROM lead_tags lt WHERE lt.lead_id = l.lead_id) t) tg ON true
      LEFT JOIN LATERAL (
          SELECT s.agent_score, s.outcome_score
            FROM call_scores s JOIN calls c ON c.call_id = s.call_id
           WHERE c.lead_id = l.lead_id
           ORDER BY c.created_at DESC LIMIT 1) sc ON true
"""


def _tags_for(lead_id):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT tag FROM lead_tags WHERE lead_id = %s '
                        'ORDER BY tag', (lead_id,))
            return [r['tag'] for r in cur.fetchall()]


def _clean_tag(raw: str) -> str:
    """Lowercased, trimmed, collapsed. 'Big Firm' and 'big  firm' are the
    same tag - stored twice they filter as two and neither finds everything."""
    return re.sub(r'\s+', ' ', (raw or '').strip().lower())[:40]


def _all_tags():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT tag, count(*) AS n FROM lead_tags '
                        'GROUP BY tag ORDER BY n DESC, tag')
            return cur.fetchall()


def _why_row(conn, lead_id):
    """
    The leads-list row for ONE lead.

    Built from _LEAD_JOINS - the same joins the list uses - so the counts in
    the line on lead detail and the line in a search result are computed by
    the same SQL, not by two queries that agree today.
    """
    with conn.cursor() as cur:
        cur.execute(f"""SELECT l.lead_id, l.status, l.stage, l.dm_name,
                               l.dm_email, l.dm_email_confirmed,
                               l.stage_changed_at, l.emailed_at, l.replied_at,
                               l.next_attempt_at, l.archived_at,
                               l.archive_reason, l.returns_at,
                               ca.call_count, ca.human_calls, tg.tags,
                               ck.clicks, em.email_count,
                               ({_EMAIL_STATE.strip()}) AS email_state
                        {_LEAD_JOINS}
                        WHERE l.lead_id = %s""", (lead_id,))
        return cur.fetchone()


def _distinct_states():
    """States that actually appear, so the dropdown offers only real ones."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT state FROM leads
                            WHERE coalesce(state,'') <> '' ORDER BY state""")
            return [r['state'] for r in cur.fetchall()]


def _chips(q, status, stage, needs_you, email_state, campaign_id, state, city,
           has_email, agent_min, agent_max, outcome_min, outcome_max,
           vol_min, vol_max, called, tag='', camp_names=None):
    """
    The active filters, each removable.

    A filter you cannot see is a filter you forget you set - and then the
    count looks wrong and the list looks broken. Every one shows, with the key
    to drop so removing it is one click rather than editing a URL.
    """
    out = []

    def add(key, label, *keys):
        out.append({'label': label, 'clear_keys': list(keys) or [key]})

    if q:
        add('q', f'search: {q}')
    if status:
        add('status', f'status: {status}')
    if stage:
        add('stage', f'stage: {stage}')
    if needs_you:
        add('needs_you', 'needs you')
    if email_state:
        add('email_state', f'email: {email_state}')
    if campaign_id == 'none':
        add('campaign_id', 'no campaign')
    elif campaign_id:
        add('campaign_id',
            f'campaign: {(camp_names or {}).get(str(campaign_id), campaign_id)}')
    if tag:
        add('tag', f'tag: {tag}')
    if state:
        add('state', f'state: {state}')
    if city:
        add('city', f'city: {city}')
    if has_email:
        add('has_email', 'has an email' if has_email == 'yes' else 'no email')
    if called:
        add('called', 'called' if called == 'yes' else 'never called')
    if agent_min or agent_max:
        add('agent', f'agent {agent_min or "0"}-{agent_max or "10"}',
            'agent_min', 'agent_max')
    if outcome_min or outcome_max:
        add('outcome', f'outcome {outcome_min or "0"}-{outcome_max or "10"}',
            'outcome_min', 'outcome_max')
    if vol_min or vol_max:
        add('vol', f'demands/mo {vol_min or "0"}-{vol_max or "any"}',
            'vol_min', 'vol_max')
    return out


def _lead_query(q, status, stage, needs_you, limit, offset, email_state='',
                campaign_id='', sort='recent', direction='desc', state='',
                city='', has_email='', agent_min='', agent_max='',
                outcome_min='', outcome_max='', vol_min='', vol_max='',
                called='', tag=''):
    where, params = ["1=1"], []
    if q:
        where.append("(l.company ILIKE %s OR l.phone_e164 ILIKE %s "
                     "OR l.dm_email ILIKE %s OR l.dm_name ILIKE %s "
                     "OR l.city ILIKE %s OR l.external_ref ILIKE %s)")
        params += [f'%{q}%'] * 6
    if status:
        where.append("l.status = %s"); params.append(status)
    else:
        # ARCHIVED IS OUT OF EVERY WORKING VIEW. A resting lead is not work,
        # and at 1,085 leads a few hundred archived rows would bury the ones
        # that need doing. Reachable only by asking for it: status=archived.
        #
        # This sits in _lead_query rather than on the page so the COUNT and
        # the bulk "add all matching" inherit it - the count drifting from
        # the list has already shipped three times, and a bulk add that
        # swept archived leads back onto a campaign would undo the rest.
        where.append("l.status <> 'archived'")
    if stage:
        where.append("l.stage = %s"); params.append(stage)
    if needs_you:
        where.append(_NEEDS_YOU_PREDICATE)
    if email_state in EMAIL_STATES:
        where.append(f'({_EMAIL_STATE.strip()}) = %s'); params.append(email_state)
    if campaign_id == 'none':
        where.append('l.campaign_id IS NULL')      # in the pool, on no campaign
    elif campaign_id:
        where.append('l.campaign_id = %s'); params.append(campaign_id)
    if tag:
        # EXISTS, not a join: joining lead_tags would multiply a lead by its
        # tags and the count would read higher than the rows on screen - the
        # count-drifts-from-the-list bug, which has now shipped three times.
        where.append('EXISTS (SELECT 1 FROM lead_tags lt2 '
                     'WHERE lt2.lead_id = l.lead_id AND lt2.tag = %s)')
        params.append(_clean_tag(tag))
    if state:
        where.append('l.state = %s'); params.append(state)
    if city:
        where.append('l.city ILIKE %s'); params.append(f'%{city}%')
    if has_email == 'yes':
        where.append("coalesce(l.dm_email,'') <> ''")
    elif has_email == 'no':
        where.append("coalesce(l.dm_email,'') = ''")
    if called == 'yes':
        where.append('l.first_dialed_at IS NOT NULL')
    elif called == 'no':
        where.append('l.first_dialed_at IS NULL')
    _score_range(where, params, 'sc.agent_score', agent_min, agent_max)
    _score_range(where, params, 'sc.outcome_score', outcome_min, outcome_max)
    # A volume filter EXCLUDES unknowns rather than treating them as zero -
    # "she did not answer" is not "sends none", and a range of 0-10 that
    # swept up every unanswered lead would be silently wrong.
    _score_range(where, params, 'l.demands_per_month', vol_min, vol_max)
    sql = f"""
        SELECT l.*,
               ({_EMAIL_STATE.strip()}) AS email_state,
               -- call_count now comes from the shared lateral alongside
               -- human_calls, so the list and lead detail cannot disagree
               -- about how many times a firm has been called.
               ca.call_count, ca.human_calls, tg.tags,
               sc.agent_score  AS last_agent,
               sc.outcome_score AS last_outcome_score,
               ck.clicks, ck.first_minutes,
               em.email_count, em.last_email_at,
               -- A RECEPTIONIST'S ESTIMATE. Sortable so the big firms can be
               -- worked first; NULLS LAST because "did not answer" is not
               -- "sends none" and must never sort as zero.
               l.demands_per_month, l.demands_per_month_raw,
               cc.name AS campaign_name, cc.is_running AS campaign_running
          {_LEAD_JOINS}
           WHERE {' AND '.join(where)}
         ORDER BY {{order}}
         LIMIT %s OFFSET %s"""
    sql = sql.replace('{order}', _order_by(sort, direction))
    return sql, params + [limit, offset], where, params


@router.get('/', response_class=HTMLResponse)
def leads_list(request: Request, q: str = '', status: str = '', stage: str = '',
               needs_you: str = '', email_state: str = '', campaign_id: str = '',
               sort: str = 'recent', dir: str = 'desc',
               state: str = '', city: str = '', has_email: str = '',
               agent_min: str = '', agent_max: str = '',
               outcome_min: str = '', outcome_max: str = '',
               vol_min: str = '', vol_max: str = '', called: str = '',
               tag: str = '', page: int = 1, per: int = PAGE, msg: str = ''):
    """THE LANDING PAGE. Where each firm stands, not a numbers dashboard."""
    cfg = _cfg()
    page = max(1, page)
    per = per if per in PAGE_SIZES else PAGE
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
        sql, params, where, cparams = _lead_query(
            q, status, stage, needs_you, per, (page - 1) * per, email_state,
            campaign_id, sort, dir, state, city, has_email, agent_min,
            agent_max, outcome_min, outcome_max, vol_min, vol_max, called,
            tag)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            # SAME GENERATOR as lead detail, over a row built by the same
            # joins. Two implementations would drift, and the one on the
            # list is the one that would quietly go stale.
            for r in rows:
                r['why'] = _why.line(r)
            # The same draft join as the list query - the email-state predicate
            # references it, and a count that cannot see `d` would 500 or, worse,
            # silently disagree with the rows on screen.
            cur.execute(
                f"""SELECT count(*) AS n {_LEAD_JOINS}
                    WHERE {' AND '.join(where)}""",
                cparams)
            total = cur.fetchone()['n']
    for r in rows:
        r['agent_class'] = _score_class(r.get('last_agent'))
        r['outcome_class'] = _score_class(r.get('last_outcome_score'))
    qs = urllib.parse.urlencode(
        {k: v for k, v in
         (('q', q), ('status', status), ('stage', stage),
          ('needs_you', needs_you), ('email_state', email_state),
          ('campaign_id', campaign_id),
          ('sort', sort if sort != DEFAULT_SORT else ''),
          ('dir', dir if dir != DEFAULT_DIR else ''),
          ('state', state), ('city', city), ('has_email', has_email),
          ('agent_min', agent_min), ('agent_max', agent_max),
          ('outcome_min', outcome_min), ('outcome_max', outcome_max),
          ('vol_min', vol_min), ('vol_max', vol_max), ('called', called),
          ('per', per if per != PAGE else ''))
         if v})
    # ONE chips object. The removable filter pills on the page and the words
    # in the bulk-add confirm are the same list, so they cannot disagree
    # about what is filtered.
    _chip_list = _chips(q, status, stage, needs_you, email_state, campaign_id,
                        state, city, has_email, agent_min, agent_max,
                        outcome_min, outcome_max, vol_min, vol_max, called,
                        tag,
                        {str(c['campaign_id']): c['name']
                         for c in campaigns.list_all()})
    return templates.TemplateResponse(request, 'leads.html', {
        'hdr': hdr, 'leads': rows, 'q': q, 'status': status,
        'stage': stage, 'needs_you': needs_you, 'statuses': STATUSES,
        'email_state': email_state, 'per': per, 'page_sizes': PAGE_SIZES,
        # The count and the words for the bulk-add confirm. `total` is the
        # matching set, which is what that button acts on - not the page.
        'filter_desc': _filter_description(_chip_list),
        'campaign_id': campaign_id, 'sort': sort, 'dir': dir,
        'state': state, 'city': city, 'has_email': has_email,
        'agent_min': agent_min, 'agent_max': agent_max,
        'outcome_min': outcome_min, 'outcome_max': outcome_max,
        'vol_min': vol_min, 'vol_max': vol_max, 'called': called,
        'chips': _chip_list,
        'states': _distinct_states(),
        'tag': tag, 'all_tags': _all_tags(),
        'stages': STAGES, 'total': total, 'qs': qs, 'page': page, 'msg': msg,
        'campaigns': campaigns.list_all(), 'running': campaigns.running(),
        'pages': max(1, (total + per - 1) // per)})


def _filter_description(chips) -> str:
    """
    The active filter, in words, for the confirm on "add all N matching".

    BUILT FROM _chips, not from its own list. It used to enumerate six of the
    seventeen filters _lead_query accepts, so the confirm could say
    "status = new" while the button was about to add every lead in North
    Carolina too. That is the count-drifts-from-the-list fault one screen
    further on, and the fix is the same one: a single place that knows what a
    filter is.

    "1,100 matching" is not enough on its own - the words are what makes it
    checkable before it is clicked.
    """
    return ', '.join(c['label'] for c in chips) or 'NO FILTER - every lead'


@router.post('/leads/queue-all')
async def leads_queue_all(request: Request):
    """
    Add EVERY lead matching the current filter, not just this page.

    Separate from the page-scoped control on purpose. Select-all stays
    page-scoped so Sean cannot queue leads he has not looked at; this is the
    deliberate opposite, and it says out loud how many and on what filter.

    It re-runs the SAME filter server-side rather than trusting a count posted
    from the page - the list could have changed since it rendered, and a
    hidden field saying "1100" is a number the browser was told, not a number
    the database agrees with.
    """
    form = await request.form()
    campaign_id_target = form.get('campaign_id') or None
    if not campaign_id_target:
        return RedirectResponse('/?msg=pick+a+campaign+first', status_code=303)
    camp = campaigns.get(campaign_id_target)
    if camp is None:
        return RedirectResponse('/?msg=no+such+campaign', status_code=303)

    # EVERY filter, not a subset. The first version read six of them, so a
    # state or score filter was silently ignored and the button added a set
    # nobody meant - the exact failure this control was built to avoid.
    # _lead_query's signature is the single source of what a filter is.
    f = {k: form.get(k, '') for k in
         ('q', 'status', 'stage', 'needs_you', 'email_state',
          'campaign_id_filter', 'state', 'city', 'has_email',
          'agent_min', 'agent_max', 'outcome_min', 'outcome_max',
          'vol_min', 'vol_max', 'called', 'tag')}
    _, _, where, cparams = _lead_query(
        f['q'], f['status'], f['stage'], f['needs_you'], 1, 0,
        f['email_state'], f['campaign_id_filter'], DEFAULT_SORT, DEFAULT_DIR,
        f['state'], f['city'], f['has_email'], f['agent_min'], f['agent_max'],
        f['outcome_min'], f['outcome_max'], f['vol_min'], f['vol_max'],
        f['called'], f['tag'])

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE leads l
                       SET campaign_id = %s, pool_status = 'active',
                           updated_at = now()
                     WHERE l.lead_id IN (
                         SELECT l.lead_id {_LEAD_JOINS}
                          WHERE {' AND '.join(where)})
                       AND l.pool_status <> 'done'""",
                [campaign_id_target] + cparams)
            n = cur.rowcount
    msg = (f"{n} lead(s) added to {camp['name']} and queued. "
           f"Nothing dials until the campaign is running.")
    return RedirectResponse(f'/?msg={urllib.parse.quote(msg)}', status_code=303)


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
        why_line = _why.line(_why_row(conn, lead_id))
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM suppression WHERE phone_e164 = %s',
                        (lead['phone_e164'],))
            suppressed = cur.fetchone()
            cur.execute('SELECT * FROM email_drafts WHERE lead_id = %s', (lead_id,))
            draft = cur.fetchone()

            # Every call, with both scores and what they actually said.
            cur.execute(
                """SELECT c.*, s.agent_score, s.outcome_score, s.agent_deductions,
                          s.rules_violated,
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
            'rules_violated': c['rules_violated'],
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
        'draft': draft, 'campaign': camp,
        'manual_statuses': MANUAL_STATUSES,
        'why_line': why_line,
        'tags': _tags_for(lead_id),
        'archive_reasons': _archive_mod.REASONS,
        'clicks': clicks_mod.summary(lead['lead_id'])})


def _int_or_none(v):
    """Blank means NOT ANSWERED, which is null - never zero."""
    v = (v or '').strip()
    if not v:
        return None
    try:
        n = int(v)
    except ValueError:
        return None
    return n if 0 <= n <= 10000 else None


@router.post('/leads/{lead_id}/edit')
def lead_edit(lead_id: str, dm_name: str = Form(''), dm_title: str = Form(''),
              dm_email: str = Form(''), dm_email_confirmed: str = Form(''),
              demands_per_month: str = Form(''), notes: str = Form('')):
    """Fix a wrong email. Every edit lands on the timeline."""
    confirmed = {'true': True, 'false': False}.get(dm_email_confirmed, None)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT dm_email FROM leads WHERE lead_id = %s', (lead_id,))
            prev = cur.fetchone()
            cur.execute(
                """UPDATE leads SET dm_name = NULLIF(%s,''), dm_title = NULLIF(%s,''),
                          dm_email = NULLIF(%s,''), dm_email_confirmed = %s,
                          demands_per_month = %s,
                          notes = NULLIF(%s,''), updated_at = now()
                    WHERE lead_id = %s""",
                (dm_name.strip(), dm_title.strip(), dm_email.strip(),
                 confirmed, _int_or_none(demands_per_month),
                 notes.strip(), lead_id))
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

    # CONFIRMING BY HAND MUST PRODUCE A DRAFT.
    #
    # There was no path from human_review to a draft at all: generate_for()
    # refuses an unconfirmed email, and the only caller was the "regenerate"
    # button, which the page hides when no draft exists. So a lead the agent
    # failed to get a confirmation for was stuck - correcting the address and
    # ticking confirmed produced nothing, with no button to press.
    if confirmed is True and dm_email.strip():
        # ADVANCE THE STAGE TOO. A confirmed email is the L1 -> L2 event
        # whether the agent captured it or a person typed it: at L2 we hold the
        # address and OWE them a send.
        #
        # Without this the lead sat at L1 with a draft it could never send -
        # mark_emailed only fires at L2, so "Send now" refused every time.
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT stage FROM leads WHERE lead_id = %s', (lead_id,))
                r = cur.fetchone()
                if r and r['stage'] == 'L1':
                    stages.advance_to_l2(cur, lead_id,
                                         source='email confirmed by hand')
        if drafts_mod.get(lead_id) is None:
            if drafts_mod.generate_for(lead_id):
                msg = msg.rstrip('.') + '. Draft generated.'
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


@router.post('/leads/{lead_id}/draft/send')
def draft_send(lead_id: str):
    """
    SEND NOW. The app puts it on the wire, through the dev allowlist.

    Separate from "I sent it", which only stamps. Two buttons because they are
    two different claims about the world, and only one of them can be checked.
    """
    from api import sender as sender_mod
    r = sender_mod.send_manual(_cfg(), lead_id)
    msg = ('Sent.' if r['sent']
           else f"NOT SENT - {r['detail']}")
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}#draft',
                            status_code=303)


@router.post('/leads/{lead_id}/emailed')
def lead_emailed(lead_id: str, emailed_by: str = Form('operator')):
    """
    "I emailed them" - records the send. The lead stays at L2.

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


# Statuses an operator may set by hand.
#
# EXCLUDED, deliberately, and each for its own reason:
#   dnc      - must go through /dnc, which writes the SUPPRESSION row in the
#              same transaction. Setting the status alone would leave a lead
#              that LOOKS suppressed and is still dialable. Suppression is the
#              highest-liability object here; it does not get a shortcut.
#   dialing  - a transient claim state owned by the dialer. Set by hand it
#              strands the lead: claimed forever, never selected again.
# Both are easy to add back if Sean wants them; a stranded lead and an
# unsuppressed DNC are not as easy to undo.
# Label and hint per ladder. Busy first because it is the shortest and the
# reason why is the least obvious.
def _retry_fields(max_attempts, busy, no_answer, voicemail) -> dict:
    """
    Only the ladders that were actually SUBMITTED.

    A field that is absent is not a field set to empty. Treating the two
    alike meant any older form that posts to this route - and every test that
    does - had its whole save rejected for an empty ladder it never sent.
    An omitted ladder must leave the stored one alone.
    """
    out = {}
    if max_attempts is not None:
        out['max_attempts'] = max_attempts
    for outcome, raw in (('busy', busy), ('no_answer', no_answer),
                         ('voicemail', voicemail)):
        if raw is not None:
            out[f'retry_{outcome}'] = _split_ladder(raw)
    return out


def _split_ladder(raw: str):
    """"15m, 1h, 4h, next_day" -> the rungs. Commas or spaces; a person
    typing a ladder should not have to think about which."""
    return [p for p in re.split(r'[,\s]+', (raw or '').strip()) if p]


RETRY_ROWS = (
    ('busy', 'Busy', 'A human is there - come back soonest.'),
    ('no_answer', 'No answer', 'Nobody picked up.'),
    ('voicemail', 'Voicemail', 'The number works, the desk is unattended.'),
)


MANUAL_STATUSES = (
    # where the dialer left it
    'new', 'queued', 'completed', 'callback', 'no_answer', 'email_path',
    'max_attempts', 'failed', 'paused', 'human_review', 'demo_pending',
    # the pipeline
    'emailed', 'engaged', 'demo_booked', 'won', 'lost', 'lost_no_response',
    'bad_email',
)


@router.post('/leads/{lead_id}/tags')
def lead_tag_add(lead_id: str, tag: str = Form(...),
                 tagged_by: str = Form('operator')):
    """
    FREE TEXT, on purpose. A fixed vocabulary is a list someone has to extend
    in code every time Sean needs a word he had not thought of - "referred by
    X", "call after tax season". segment covers the one case it was built
    for; this covers the rest.

    Several at once, comma separated, because tagging is done in one pass.
    """
    added = [t for t in (_clean_tag(x) for x in tag.split(',')) if t]
    if not added:
        msg = 'REJECTED: an empty tag.'
    else:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                for t in added:
                    cur.execute(
                        """INSERT INTO lead_tags (lead_id, tag, created_by)
                           VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (lead_id, t, tagged_by))
        msg = f'tagged: {", ".join(added)}'
    return RedirectResponse(
        f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}', status_code=303)


@router.post('/leads/{lead_id}/tags/remove')
def lead_tag_remove(lead_id: str, tag: str = Form(...)):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM lead_tags WHERE lead_id=%s AND tag=%s',
                        (lead_id, _clean_tag(tag)))
    return RedirectResponse(
        f'/leads/{lead_id}?saved={urllib.parse.quote("removed " + tag)}',
        status_code=303)


@router.post('/leads/{lead_id}/archive')
def lead_archive(lead_id: str, reason: str = Form(...),
                 archived_by: str = Form('operator'), note: str = Form('')):
    """
    ARCHIVE NEEDS A REASON, which is why it is a button and not a dropdown
    entry. Setting status='archived' by hand would leave archived_at and
    returns_at null, and the sweep reads returns_at - so the lead would rest
    forever with nothing on screen saying why.

    Same shape as /dnc: the status and the facts that make it mean something
    are written in one transaction, and that route is the only way in.
    """
    from api import archive as _archive
    try:
        _archive.archive(lead_id, reason, by=archived_by, note=note)
        msg = f'Archived ({reason}). Returns to the pool in 6 months.'
    except _archive.ArchiveRefused as exc:
        msg = f'REJECTED: {exc}'
    return RedirectResponse(
        f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}', status_code=303)


@router.post('/leads/{lead_id}/unarchive')
def lead_unarchive(lead_id: str, unarchived_by: str = Form('operator')):
    """Back to the pool early. Clears nothing but the lead's own columns."""
    from api import archive as _archive
    row = _archive.unarchive(lead_id, by=unarchived_by)
    msg = ('Returned to the pool. Suppression and the email do-not-send list '
           'are untouched.') if row else 'That lead is not archived.'
    return RedirectResponse(
        f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}', status_code=303)


@router.post('/leads/{lead_id}/status')
def lead_status(lead_id: str, status: str = Form(...),
                changed_by: str = Form('operator')):
    """
    THE OPERATOR OVERRULES THE SYSTEM.

    A lead the scorer flagged human_review used to be stuck there: nothing but
    the scorer could set it, so a flag Sean had already dealt with held the
    lead forever.

    Every change lands on the timeline saying it was done BY HAND. That
    distinction is the point - a hand correction must never be mistakable for
    an agent capture, and this system has no login, so "who" can only mean
    "a person at the CRM" rather than "the system".
    """
    if status not in MANUAL_STATUSES:
        msg = (f'REJECTED: {status!r} cannot be set by hand. '
               f'Use the DNC button for dnc; "dialing" is the dialer\'s; '
               f'use Archive for archived - it needs a reason and a return '
               f'date, and a bare status would rest the lead forever.')
        return RedirectResponse(
            f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}', status_code=303)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM leads WHERE lead_id = %s', (lead_id,))
            row = cur.fetchone()
            if row is None:
                return RedirectResponse('/?msg=no+such+lead', status_code=303)
            was = row['status']
            if was == status:
                return RedirectResponse(
                    f'/leads/{lead_id}?saved={urllib.parse.quote("Already " + status)}',
                    status_code=303)
            cur.execute("""UPDATE leads SET status = %s, updated_at = now()
                            WHERE lead_id = %s""", (status, lead_id))
            cur.execute(
                """INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s,'status','status changed BY HAND',%s)""",
                (lead_id, f'{was} -> {status}  (by {changed_by or "operator"})'))
    msg = f'Status set to {status} by hand (was {was}).'
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/leads/{lead_id}/reply')
def lead_reply(lead_id: str, note: str = Form(''), by: str = Form('operator')):
    """
    "I GOT A REPLY" - manual reply detection, and the guard the drip will read.

    Sean reads every reply at this volume, so a person ticking a box IS the
    detector. When automatic ingest lands it calls the SAME
    stages.record_reply(), as a second writer to one field - not a
    replacement - so the dialer's guard, the auto-send gate and the drip all
    keep reading one fact from one place.
    """
    ok = stages.record_reply(lead_id, note=note, by=by or 'operator')
    msg = ('Reply recorded. Nothing will auto-contact this lead again.'
           if ok else 'A reply was already recorded for this lead.')
    return RedirectResponse(f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}',
                            status_code=303)


@router.post('/leads/{lead_id}/reply/undo')
def lead_reply_undo(lead_id: str, by: str = Form('operator')):
    """Untick it. Writes to the timeline exactly as the tick did."""
    ok = stages.clear_reply(lead_id, by=by or 'operator')
    msg = ('Reply record withdrawn - it stays on the timeline. Check the '
           'status: a click can also have made this lead engaged.'
           if ok else 'No reply was recorded for this lead.')
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
        # The ladders, and how many rungs of each can actually fire. A rung
        # only applies if another attempt follows it, so a four-rung ladder
        # under max attempts 4 has a fourth rung that is decoration.
        'retry_rows': RETRY_ROWS,
        'reachable': {o: _rl.reachable(camp[f'retry_{o}'], camp['max_attempts'])
                      for o in _rl.COLUMNS},
        'hours_left': round(remaining / per_hour, 1) if per_hour else 0,
        'windows': campaigns.windows(campaign_id), 'days': DAYS,
        'prompt_rows': {'L1': prompts_mod.listing(cfg, 'L1',
                                                  camp['agent_l1_version'])},
        'sender_options': _sender_opts[0], 'sender_error': _sender_opts[1],
        'placeholders': drafts_mod.PLACEHOLDERS,
        'preview': drafts_mod.preview(camp, lead=pv_lead),
        'preview_lead': pv_lead, 'preview_real': pv_real,
        'running': campaigns.running()})


@router.get('/funnel', response_class=HTMLResponse)
def funnel_page(request: Request, campaign_id: str = '', agent_version: str = '',
                date_from: str = '', date_to: str = '', status: str = ''):
    """
    Where leads stop, and which step is worst.

    A COHORT funnel: the date range selects the leads FIRST DIALED in that
    window and follows that same set down. Mixing a Monday call with a
    Thursday email would produce percentages nobody can read.
    """
    cfg = _cfg()
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
    data = funnel_mod.build(campaign_id, agent_version, date_from, date_to, status)
    fc = forecast_mod.build(campaign_id)
    return templates.TemplateResponse(request, 'funnel.html', {
        'hdr': hdr, 'rows': data['rows'], 'counts': data['counts'],
        'thin': data['thin'],
        'campaigns': campaigns.list_all(),
        'versions': funnel_mod.versions_seen(campaign_id),
        'campaign_id': campaign_id, 'agent_version': agent_version,
        'date_from': date_from, 'date_to': date_to, 'fc': fc,
        'status': status, 'statuses': STATUSES})


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
        'prompt_rows': {'L1': prompts_mod.listing(cfg, 'L1')},
        'campaigns': campaigns.list_all()})


@router.post('/prompts/note')
def prompts_note(stage: str = Form('L1'), agent_version: int = Form(...),
                 note: str = Form('')):
    cfg = _cfg()
    agent_id = cfg.AGENT_L1
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
    msg = f"re-fetched {a['fetched']} of {a['versions']} versions from Retell"
    return RedirectResponse(f'/prompts?msg={urllib.parse.quote(msg)}', status_code=303)


@router.post('/campaign/{campaign_id}/save')
def campaign_save(campaign_id: str, name: str = Form(...), notes: str = Form(''),
                  agent_l1_version: int = Form(...),
                  sender_email: str = Form(...), sender_name: str = Form(...),
                  sender_company_line: str = Form(...), daily_cap: int = Form(...),
                  max_concurrent: int = Form(...), dial_interval_min: int = Form(...),
                  dial_interval_max: int = Form(...),
                  max_attempts: int = Form(None),
                  retry_busy: str = Form(None),
                  retry_no_answer: str = Form(None),
                  retry_voicemail: str = Form(None)):
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
                         sender_email=sender_email, sender_name=sender_name,
                         sender_company_line=sender_company_line,
                         daily_cap=daily_cap, max_concurrent=max_concurrent,
                         dial_interval_min=dial_interval_min,
                         dial_interval_max=dial_interval_max,
                         **_retry_fields(max_attempts, retry_busy,
                                         retry_no_answer, retry_voicemail))
        msg = f'saved - prompt v{agent_l1_version} is now live'
    except Exception as exc:
        # A raw constraint violation is a wall of Postgres. Say what the
        # operator did wrong, in the terms they used.
        text = str(exc)
        if 'agent_l1_version_check' in text:
            msg = (f'REJECTED: v{agent_l1_version} is not a valid version '
                   f'number. Nothing changed.')
        elif 'daily_cap_check' in text:
            msg = f'REJECTED: daily cap must be 1-5000. Nothing changed.'
        elif 'max_concurrent_check' in text:
            msg = f'REJECTED: calls in flight must be 1-10. Nothing changed.'
        elif isinstance(exc, _rl.BadLadder):
            # Say which rung and why, in the words on the screen. A raw
            # constraint here would only say the array was text.
            msg = f'REJECTED: {text}. Nothing changed.'
        elif 'max_attempts_check' in text:
            msg = 'REJECTED: max attempts must be 1-10. Nothing changed.'
        elif 'dial_interval' in text:
            msg = ('REJECTED: the gap must be 15-3600s (min) and 15-7200s '
                   '(max). Nothing changed.')
        else:
            msg = f'REJECTED: {text[:150]}'
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
    # Back to /leads, not /campaign: uploading is about LEADS, and the whole
    # point of moving the form was to stop bouncing between two screens to do
    # one thing.
    return RedirectResponse(f'/?msg={urllib.parse.quote(msg[:300])}',
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
