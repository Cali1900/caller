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
                 prompts as prompts_mod, stages, drip as _drip_mod,
                 timezones as timezones_mod,
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
            'emailed', 'clicked', 'engaged', 'demo_booked', 'won', 'lost',
            'lost_no_response', 'bad_email', 'archived',
            # An email-only lead: never called, never mailed. Its own status
            # rather than 'new' (which means waiting to be dialled, and these
            # have no phone) or 'emailed' (which would be a lie).
            'imported']
# THE FILTER STILL READS L1 / L2, because that is the vocabulary on the screen
# and on every past call and score record. The COLUMN behind it is
# leads.has_confirmed_email, a boolean (migration 035) - L2 means "we have a
# confirmed email and owe them a send". won/lost live on status, which is the
# one home for them.
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
# ⚠️ 'EMAILED AND ON NO DRIP' IS THE SAFETY NET FOR A STALLED SEQUENCE.
#
# Membership is DERIVED from status: a lead is on every running drip whose
# accepted_statuses contain its status. When none do - no drip running, or none
# and no default set, or a default pointing at a stopped one - email 1 goes out
# and NOTHING FOLLOWS UP. There is no error and no failed send: the lead simply
# sits at 'emailed' forever.
#
# It used to be visible only by opening that lead, which means finding out one
# firm at a time. A lead that has stopped and cannot say so is the failure this
# whole system is built to avoid, so it surfaces here.
#
# Excludes replied/archived/terminal: those have stopped ON PURPOSE.
# Activity kinds whose events are rendered from email_sends/email_clicks instead,
# with the step, the subject and the click delay. Skipped when walking `activity`
# so one send is one timeline entry. NAMED, not matched on prose - except the drip
# row, which shares its kind with STOP events that must be kept.
_EMAIL_ACTIVITY_KINDS = ('email_sent', 'sent_manual', 'click')
_DRIP_SEND_SUMMARY = re.compile(r'^drip step \d+ sent$')

_STALLED_AFTER_EMAIL = """
    (l.emailed_at IS NOT NULL
     -- ⚠️ "NO DRIP ACCEPTS THIS LEAD" replaces "drip_campaign_id IS NULL".
     -- Membership is derived, so the question is no longer "was it assigned"
     -- but "does any RUNNING drip take its status" - the same failure, asked of
     -- the mechanism that now decides it.
     AND NOT EXISTS (SELECT 1 FROM campaign_configs dc
                      WHERE dc.type = 'drip' AND dc.is_running
                        AND l.status = ANY(dc.accepted_statuses))
     AND l.replied_at IS NULL
     AND l.status NOT IN ('archived','dnc','won','lost','bad_email',
                          'demo_booked','lost_no_response'))
"""

_NEEDS_YOU_PREDICATE = f"""
    (l.status IN ('human_review', 'demo_pending')
     OR (l.dm_email IS NOT NULL AND l.dm_email_confirmed IS NOT TRUE
         AND l.lead_source <> 'import')
     OR {_STALLED_AFTER_EMAIL.strip()}
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
    # Sorts by the boolean; false (L1) first, which is the working order.
    'stage':     'l.has_confirmed_email',
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
        cur.execute(f"""SELECT l.lead_id, l.status, l.has_confirmed_email, l.dm_name,
                               l.dm_email, l.dm_email_confirmed,
                               l.email_confirmed_at, l.emailed_at, l.replied_at,
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
        # L1 / L2 on the screen, a boolean in the column.
        where.append('l.has_confirmed_email = %s')
        params.append(stage == 'L2')
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
            # ⚠️ NO `why` LINE ON THE LIST. It is computed per row for lead
            # DETAIL only: on the list it wrapped every row onto three lines and
            # made the table unscannable. Not computed here at all rather than
            # computed and hidden, because a per-row generator over hundreds of
            # rows is work nobody reads.
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
            # ⚠️ EMAILS FROM THEIR SOURCE TABLES, not from the activity summary.
            # The activity row says "drip step 2 sent" as prose; email_sends knows
            # the step POSITION, the subject and the send time, and email_clicks
            # knows which send was clicked and how long after. Rendering the prose
            # threw all of that away, which is why the timeline had calls in
            # detail and emails as one grey line.
            cur.execute(
                """SELECT es.send_id, es.seq, es.sent_at, es.prepared_at,
                          es.subject, es.sent_by, es.to_email,
                          st.position, st.subject AS step_subject
                     FROM email_sends es
                     LEFT JOIN drip_steps st ON st.step_id = es.step_id
                    WHERE es.lead_id = %s
                    ORDER BY coalesce(es.sent_at, es.prepared_at) DESC""",
                (lead_id,))
            sends = cur.fetchall()
            cur.execute(
                """SELECT ec.clicked_at, ec.minutes_since_sent, st.position,
                          es.subject, es.seq
                     FROM email_clicks ec
                     LEFT JOIN email_sends es ON es.send_id = ec.send_id
                     LEFT JOIN drip_steps st  ON st.step_id = es.step_id
                    WHERE ec.lead_id = %s
                    ORDER BY ec.clicked_at DESC""", (lead_id,))
            email_clicks = cur.fetchall()
            # BOUNCES AND REFUSALS. A bounce that shows nowhere is a lead failing
            # silently - the argument that put 'bounced' on the status filter.
            cur.execute(
                """SELECT created_at, outcome, detail, to_email
                     FROM email_audit
                    WHERE lead_id = %s
                      AND outcome NOT IN ('sent_drip','sent','sent_manual')
                    ORDER BY created_at DESC LIMIT 40""", (lead_id,))
            email_problems = cur.fetchall()

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
    # ⚠️ SENDS AND CLICKS ARE RENDERED FROM email_sends/email_clicks, so their
    # activity rows are SKIPPED here or every email would appear twice - once in
    # detail and once as prose. _EMAIL_ACTIVITY_KINDS names them explicitly
    # rather than matching on summary text, and the one-entry-per-send property
    # has its own test.
    for a in acts:
        if a['kind'] == 'call':
            continue          # already rendered above, with its scores
        if a['kind'] in _EMAIL_ACTIVITY_KINDS:
            continue          # rendered below, with the step and the subject
        if a['kind'] == 'drip' and _DRIP_SEND_SUMMARY.match(a['summary'] or ''):
            continue          # ditto - but a drip STOP is kept, it says why
        timeline.append({'at': a['created_at'], 'kind': a['kind'],
                         'stage': a['stage'], 'summary': a['summary'],
                         'detail': a['detail']})
    for e in sends:
        # A step position when the send belongs to a drip step; email 1 from the
        # call path has step_id NULL, and calling that "step 1" would be a guess.
        what = (f'step {e["position"]} sent' if e['position']
                else 'email 1 sent')
        timeline.append({
            'at': e['sent_at'] or e['prepared_at'], 'kind': 'email',
            'summary': what, 'subject': e['subject'] or e['step_subject'],
            'sent': e['sent_at'] is not None, 'to_email': e['to_email'],
            'sent_by': e['sent_by'], 'step': e['position']})
    for cl in email_clicks:
        timeline.append({
            'at': cl['clicked_at'], 'kind': 'click',
            'summary': ('CLICKED' + (f' - step {cl["position"]}'
                                     if cl['position'] else ' - email 1')),
            'subject': cl['subject'],
            'minutes': cl['minutes_since_sent']})
    for pr in email_problems:
        timeline.append({'at': pr['created_at'], 'kind': 'email_problem',
                         'summary': pr['outcome'].replace('_', ' '),
                         'detail': pr['detail'], 'to_email': pr['to_email']})
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
        'clicks': clicks_mod.summary(lead['lead_id']),
        # THE DRIP: which sequence, where in it, and what has actually gone.
        # ⚠️ MEMBERSHIP IS NEVER A MYSTERY. Derived means there is no column to
        # read, so the page has to SAY which drips this lead is in and why -
        # otherwise "why is this firm getting mail" has no answer on the screen.
        'qualifies': _drip_mod.qualifies_for(lead),
        # WHICH STATUSES WOULD PUT THIS LEAD ON A RUNNING DRIP, so the dropdown
        # can warn before the change rather than after the mail.
        'status_sends': _drip_mod.sending_statuses(),
        'sends': _drip_mod.sends(lead['lead_id']),
        'stop_reasons': _drip_mod.STOP_REASONS,
        # ⚠️ DATA THAT SURVIVES A RETURN AND CANNOT BE SEEN IS THE SAME FAULT AS
        # AN OUT-OF-DATE INVENTORY. archived_contacts holds what the send record
        # said before an archive return cleared it - "we emailed this firm in
        # September, it clicked twice and said no on the 8th" - and until now it
        # had a reader and tests and no way to look at it.
        'archived_contacts': _archive_mod.contacts(lead['lead_id'])})


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
def lead_edit(lead_id: str, dm_name: str = Form(None), dm_title: str = Form(None),
              dm_email: str = Form(None), dm_email_confirmed: str = Form(None),
              demands_per_month: str = Form(None), notes: str = Form(None),
              phone_e164: str = Form(''), timezone: str = Form(''),
              move_to: str = Form('')):
    """
    Fix a wrong email, or give an email-only lead a number. Every edit lands on
    the timeline.

    ⚠️ A PHONE AND A TIMEZONE ARE SAVED TOGETHER OR NOT AT ALL.

    An imported lead has neither. Adding just the number makes it LOOK dialable
    - a phone, on a campaign, queued - while windows.PREFERENCE_WINDOW joins on
    l.timezone, so a NULL there matches no window and the lead is silently
    excluded forever with nothing saying why.

    That is the archive bug's exact shape: a row that reads as workable and is
    structurally unreachable. So it REFUSES rather than saving half of what is
    needed, and says which half is missing.
    """
    confirmed = {'true': True, 'false': False}.get(dm_email_confirmed, None)
    phone_e164, timezone = phone_e164.strip(), timezone.strip()

    # ⚠️ THE TIMEZONE IS DERIVED FROM THE STATE, never asked for. Every other
    # entry path derives it (upload, add_lead), and asking here would make the
    # same fact arrive two ways - one of which a person can get wrong. If there
    # is no state there is nothing to derive from, and that is what blocks the
    # phone: said plainly, rather than offering a move that cannot work.
    if phone_e164 and not timezone:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT state FROM leads WHERE lead_id = %s',
                            (lead_id,))
                st = (cur.fetchone() or {}).get('state')
        if (st or '').strip():
            timezone, _src, _review = timezones_mod.for_state(st)
        else:
            msg = ('REJECTED: this lead has no STATE, so its timezone cannot be '
                   'derived - and the calling window is evaluated in the called '
                   'party\'s local time, so a number without one would look '
                   'dialable and never dial. Add the state first.')
            return RedirectResponse(
                f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}',
                status_code=303)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT dm_email, phone_e164 FROM leads '
                        'WHERE lead_id = %s', (lead_id,))
            prev = cur.fetchone()
            if phone_e164:
                # NULLIF keeps a blank from wiping an existing number: this form
                # is also used to edit a call-sourced lead, and an empty box
                # there means "unchanged", not "remove the phone".
                cur.execute(
                    """UPDATE leads SET phone_e164 = %s, timezone = %s,
                              tz_source = 'hand', updated_at = now()
                        WHERE lead_id = %s""",
                    (phone_e164, timezone, lead_id))
                # ⚠️ A PHONE MAKES IT CALLABLE, AND CALLABLE IS A DECISION.
                # 'imported' is not a dialable status, so adding a number alone
                # changes nothing about dialling - which is correct, and would be
                # invisible without this. Moving sets 'new' and assigns the call
                # campaign; the lead then falls out of every drip whose gate no
                # longer matches, automatically, because the gate recalculates.
                if move_to.strip():
                    target = campaigns.get(move_to.strip())
                    if target is None or target['type'] != 'call':
                        pass
                    else:
                        cur.execute(
                            """UPDATE leads SET status = 'new',
                                      campaign_id = %s, updated_at = now()
                                WHERE lead_id = %s""",
                            (target['campaign_id'], lead_id))
                        cur.execute(
                            """INSERT INTO activity
                                   (lead_id, kind, summary, detail)
                               VALUES (%s,'note','moved to a call campaign',%s)""",
                            (lead_id,
                             f'{target["name"]}. Status is now `new`, so it '
                             f'leaves any drip that accepted its old status - '
                             f'the gate recalculates, nothing had to remember '
                             f'to remove it.'))
                if not (prev or {}).get('phone_e164'):
                    cur.execute(
                        """INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'note','phone added by hand',%s)""",
                        (lead_id,
                         f'{phone_e164} ({timezone}). This lead arrived without '
                         f'a number; it is now dialable. Its lead_source is '
                         f'unchanged - that records where it came from, not '
                         f'what has happened to it since.'))
            # ⚠️ ONLY WHAT WAS POSTED. These were Form('') and written
            # unconditionally, so ANY post that omitted a field NULLed it -
            # dm_email included, which silently ends a drip. The phone above was
            # already protected against exactly this and the protection was
            # never generalised.
            #
            # ABSENT IS NOT EMPTY: None means the field was not on the form,
            # '' means the operator cleared the box on purpose. The real form
            # posts every field, so clearing still works; what changes is that a
            # PARTIAL post can no longer blank what it never mentioned.
            sets, vals = [], []
            for col, raw in (('dm_name', dm_name), ('dm_title', dm_title),
                             ('dm_email', dm_email), ('notes', notes)):
                if raw is not None:
                    sets.append(f"{col} = NULLIF(%s,'')")
                    vals.append(raw.strip())
            if dm_email_confirmed is not None:
                sets.append('dm_email_confirmed = %s')
                vals.append(confirmed)
            if demands_per_month is not None:
                sets.append('demands_per_month = %s')
                vals.append(_int_or_none(demands_per_month))
            if sets:
                cur.execute(
                    f"""UPDATE leads SET {', '.join(sets)}, updated_at = now()
                         WHERE lead_id = %s""", vals + [lead_id])
            old = (prev or {}).get('dm_email')
            changed = (dm_email is not None
                       and old != (dm_email.strip() or None))
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
                cur.execute('SELECT has_confirmed_email FROM leads '
                            'WHERE lead_id = %s', (lead_id,))
                r = cur.fetchone()
                if r and not r['has_confirmed_email']:
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
    """"15m, 1h, 4h, 1d" -> the rungs. Commas or spaces; a person typing a
    ladder should not have to think about which."""
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
    'emailed', 'clicked', 'engaged', 'demo_booked', 'won', 'lost',
    'lost_no_response', 'bad_email',
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
                changed_by: str = Form('operator'), back: str = Form('')):
    # ⚠️ `back` RETURNS THE OPERATOR WHERE THEY WERE. The status dropdown exists in
    # two places now - lead detail and the drip roster's pill - and it is ONE
    # control: same route, same rules, same timeline entry. What differs is only
    # where you land, and sending someone to a different screen than the one they
    # acted on is its own small lie about what happened.
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
            (f'{back}?msg={urllib.parse.quote(msg)}' if back else f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}'), status_code=303)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM leads WHERE lead_id = %s', (lead_id,))
            row = cur.fetchone()
            if row is None:
                return RedirectResponse('/?msg=no+such+lead', status_code=303)
            was = row['status']
            # LEAVING ARCHIVE IS NOT A STATUS CHANGE, and this refusal is the
            # exact mirror of the one above for moving INTO archived.
            #
            # A bare UPDATE here strands the lead: pool_status stays 'done',
            # campaign_id stays NULL, archived_at/returns_at stay set, and every
            # gate the return is supposed to clear (has_confirmed_email,
            # emailed_at, dm_email_confirmed, replied_at) stays set. So the lead
            # is un-archived in name only - undialable and un-emailable.
            #
            # And it is stranded PERMANENTLY, two ways: archive.return_due()
            # selects WHERE status = 'archived', so the nightly sweep can no
            # longer see it; and lead.html only renders "Return to the pool now"
            # {% if lead.status == 'archived' %}, so the one control that would
            # repair it disappears from the page. There is no route back but
            # hand SQL.
            #
            # It REFUSES rather than quietly doing the restore instead. Doing
            # something bigger than was asked is how the archive reason gets
            # lost: unarchive() snapshots the send record and clears four gates,
            # which is a great deal more than "set the status", and a dropdown
            # that silently performed it would make the timeline lie about what
            # a person did. Same discipline as /dnc being the only route to
            # suppression.
            if was == 'archived':
                msg = ('REJECTED: leaving the archive is not a status change. '
                       'Use "Return to the pool now" in the Archive section - '
                       'it clears the campaign, the attempts and the send '
                       'gates, and records the return. A bare status change '
                       'would leave the lead un-archived but unusable, and '
                       'invisible to the nightly sweep.')
                return RedirectResponse(
                    (f'{back}?msg={urllib.parse.quote(msg)}' if back else f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}'),
                    status_code=303)
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
    return RedirectResponse((f'{back}?msg={urllib.parse.quote(msg)}' if back else f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}'),
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
    # A DRIP WITH NO STEPS SENDS NOTHING, and that is worth saying on the list
    # rather than only inside the campaign. leads_total/leads_queued on the row
    # count leads.campaign_id, which is the CALL campaign - a drip needs its own
    # count off drip_campaign_id.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT campaign_id::text AS cid, count(*) AS n
                             FROM drip_steps WHERE deleted_at IS NULL
                            GROUP BY campaign_id""")
            step_counts = {r['cid']: r['n'] for r in cur.fetchall()}
    drip_counts = _drip_mod.qualifying_counts()
    return templates.TemplateResponse(request, 'campaigns.html', {
        'hdr': hdr, 'campaigns': rows, 'msg': msg,
        'step_counts': step_counts, 'drip_counts': drip_counts,
        'running': campaigns.running(), 'pending': pending})


@router.post('/campaigns/new')
def campaigns_new(name: str = Form(...), template_from: str = Form(''),
                  notes: str = Form(''), campaign_type: str = Form('call')):
    """
    TYPE IS CHOSEN HERE AND NOWHERE ELSE. It is set at CREATION and
    campaigns.update() refuses it: changing a campaign's type under live leads
    would move their whole ladder sideways.

    Defaulting to 'call' keeps the old form's behaviour for anything that posts
    without the field, and a bad value is refused by create() rather than
    silently becoming a call campaign.
    """
    try:
        row = campaigns.create(name, template_from=template_from or None,
                               campaign_type=campaign_type, notes=notes)
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


@router.post('/leads/{lead_id}/drip/stop')
def lead_drip_stop(lead_id: str, reason: str = Form('by_hand'),
                   stopped_by: str = Form('operator')):
    """
    STOP ONE LEAD, not the whole drip.

    This is the control the digest's "going out tomorrow" block points at. Reply
    detection is by hand, so the case that matters is "this firm has answered
    and I have not ticked it yet" - and the right response is to stop that one
    lead, not to pause a sequence that is working for everyone else.
    """
    try:
        ok = _drip_mod.stop(lead_id, reason, by=stopped_by)
        msg = (f'Stopped the drip for this lead ({reason}).' if ok
               else 'That lead is not on a drip.')
    except ValueError as exc:
        msg = f'REJECTED: {exc}'
    return RedirectResponse(
        f'/leads/{lead_id}?saved={urllib.parse.quote(msg)}', status_code=303)


# ⚠️ THERE IS NO "ADD TO DRIP" ROUTE ANY MORE, and that is the whole point of
# the redesign: a lead cannot be put on a drip by hand, because membership is not
# a thing that can be set. Change its STATUS and it joins every drip accepting
# that status; change it again and it leaves. The status dropdown on lead detail
# is the control, and it says which drips a change would qualify the lead for
# before you make it.


@router.post('/campaign/{campaign_id}/steps/preview')
async def campaign_step_preview(campaign_id: str, request: Request):
    """
    Render EVERY step currently in the boxes, against a chosen lead.

    THE SAME SHAPE AS /campaign/{id}/email/preview, deliberately. That endpoint
    takes the whole form and returns every variant at once, and the copy editor
    posts `new FormData(form)` at it from one delegated listener. Matching it
    means the sequence editor needs no per-element wiring - which is also why a
    cloned step previews correctly with no extra code.

    SERVER-SIDE, through the same drafts.render() and values_for() the real sends
    use. A second copy of the substitution rules in JS could drift from them, and
    a preview that drifts is worse than none because it is trusted.
    """
    camp = campaigns.get(campaign_id)
    if camp is None:
        return JSONResponse({'error': 'no such campaign'}, status_code=404)
    form = await request.form()
    # 'sample' is an explicit pick, and an unknown/absent id falls back to the
    # SAMPLE rather than to some arbitrary lead - a preview must always work,
    # and it must always say whose data it is.
    lead = drafts_mod.lead_for_preview(form.get('lead_id'))
    real = lead is not None and lead.get('lead_id') is not None
    if lead is None:
        lead = dict(drafts_mod.SAMPLE_LEAD)
        real = False
    vals = drafts_mod.values_for(lead, camp)

    import re as _re
    out = {}
    for key in form.keys():
        m = _re.fullmatch(r'body_(\d+)', key)
        if not m:
            continue
        i = m.group(1)
        subject = drafts_mod.render(form.get(f'subject_{i}') or '', vals)
        body = drafts_mod.render(form.get(f'body_{i}') or '', vals)
        # AN UNRESOLVED PLACEHOLDER IS NAMED. {{frist_name}} renders as itself
        # and is easy to miss in prose; the real send would post it verbatim.
        unknown = sorted(set(_re.findall(r'\{\{\s*([a-zA-Z_]+)\s*\}\}',
                                       subject + ' ' + body)))
        out[i] = {'subject': subject, 'body': body, 'unknown': unknown,
                  'chars': len(body), 'words': len(body.split())}
    return JSONResponse({
        'steps': out,
        'lead': {'company': lead.get('company'), 'name': lead.get('dm_name'),
                 'real': real}})


def _step_days(value):
    """
    Days -> minutes, or None when nothing was posted.

    None means "not supplied", which drip.validate() reads as 0 for step 1 -
    'immediately' is the sensible reading of a blank timing box on the FIRST
    email. A non-number is passed through so validate() refuses it with its own
    message rather than being silently coerced.
    """
    v = (value or '').strip()
    if not v:
        return None
    try:
        return int(v) * 1440
    except ValueError:
        return v


def _step_minutes(value, unit):
    """
    (number, 'm'|'h') -> minutes, or None when nothing was posted.

    None means "not supplied", which drip.validate() reads as 0 for step 1 -
    'immediately' is the sensible reading of a blank timing box on the FIRST
    email. An unrecognised unit is treated as minutes rather than guessed at:
    the smaller unit is the safer wrong answer, because it cannot silently turn
    a 15-minute delay into 15 hours.
    """
    v = (value or '').strip()
    if not v:
        return None
    try:
        n = int(v)
    except ValueError:
        return v                      # let validate() refuse it, with its message
    return n * 60 if (unit or 'm').strip().lower().startswith('h') else n


def _posted_steps(rows):
    """
    Posted rows in the shape the template reads, so a refusal re-renders them.

    Values are whatever was typed, INCLUDING the invalid one - the point is that
    the operator sees the step that was refused with their own words in it. Only
    delay_minutes is coerced, because the template divides it and a string there
    would 500 the page that is trying to explain a mistake.
    """
    out = []
    for n, r in enumerate(rows, start=1):
        try:
            mins = int(r.get('delay_minutes') or 0)
        except (TypeError, ValueError):
            mins = 0
        days = r.get('delay_days')
        out.append({'step_id': r.get('step_id') or '', 'position': n,
                    'delay_days': '' if days is None else days,
                    'delay_minutes': mins,
                    'enabled': bool(r.get('enabled')),
                    'subject': r.get('subject') or '',
                    'body': r.get('body') or ''})
    return out


@router.post('/campaign/{campaign_id}/steps')
async def campaign_steps_save(request: Request, campaign_id: str):
    """
    Save the whole sequence in one act: add, remove, reorder, edit.

    THE FORM POSTS THE SEQUENCE AS IT SHOULD BE, not a diff. Ordering is the
    order of the rows; a row with no step_id is new; a step that is not posted is
    soft-deleted. That is one atomic replace rather than four endpoints whose
    combinations have to be reasoned about.
    """
    form = await request.form()

    # ⚠️ COLLECT EVERY POSTED INDEX. This used to walk i = 0, 1, 2 ... and STOP AT
    # THE FIRST GAP, so any step whose index was not contiguous was dropped
    # SILENTLY - the save reported success and wrote fewer rows than were on the
    # screen. That is data loss with a green banner: copy someone typed,
    # destroyed, with nothing saying so.
    #
    # Indices come from the DOM, and the DOM is assembled from a server-rendered
    # list plus javascript clones. Nothing guarantees they stay contiguous - a
    # removed step, a failed clone, a double-fired handler, any of it leaves a
    # hole - and a loop that stops at the first hole treats "I could not see it"
    # as "it is not there".
    #
    # Read them all, then sort. The screen's order is the index order.
    import re as _re
    seen = set()
    for key in form.keys():
        m = _re.fullmatch(r'(?:subject|body)_(\d+)', key)
        if m:
            seen.add(int(m.group(1)))
    posted = sorted(seen)

    def _content(i):
        return ((form.get(f'subject_{i}') or '').strip()
                or (form.get(f'body_{i}') or '').strip())

    rows = []
    for i in posted:
        # ⚠️ A ROW WITH NO COPY AT ALL IS SKIPPED, which is what the template has
        # always promised ("leave blank to skip") and what the count below has
        # always assumed. It did NOT skip them, and that cost twice:
        #
        #   * an untouched blank row refused the whole save with "step 3 has no
        #     subject", so adding a step you then decided against blocked
        #     saving the two you meant;
        #   * IT MASKED THE COUNT GUARD. `kept` counts steps WITH copy, `rows`
        #     counted every posted row, so a blank row could stand in the place
        #     of a real step that got dropped - the two numbers matched and the
        #     guard stayed quiet over exactly the loss it exists to catch. A
        #     guard whose two counts measure different things is not a guard.
        #
        # Skipping is safe precisely because there is nothing to lose: no
        # subject, no body, nothing anybody typed.
        if not _content(i):
            continue
        if (form.get(f'delete_{i}') or '') != '1':
            # `enabled` is supplied EXPLICITLY, always. An unchecked checkbox
            # posts nothing, and drip._flag() defaults absent to TRUE so a
            # programmatic caller gets a normal step - so the form has to say
            # 'off' out loud rather than saying nothing.
            rows.append({'step_id': (form.get(f'step_id_{i}') or '').strip() or None,
                         'delay_days': form.get(f'delay_{i}'),
                         # STEP 1's timing is a NUMBER plus a UNIT, because
                         # "after 2 hours" typed as 120 minutes is arithmetic the
                         # operator should not be doing. Stored as minutes: one
                         # column, one scale, and the unit is presentation.
                         # STEP 1 IS MEASURED IN DAYS FROM JOINING THE DRIP.
                         # Stored in the same delay_minutes column so there is
                         # one scale in the database and the unit is
                         # presentation - the column name is now narrower than
                         # what it holds, which is the lesser evil against a
                         # second timing column that must agree with it.
                         'delay_minutes': _step_days(form.get(f'day1_{i}')),
                         'enabled': '1' if form.get(f'enabled_{i}') else '',
                         'subject': form.get(f'subject_{i}'),
                         'body': form.get(f'body_{i}')})
    # ⚠️ REFUSE IF THE COUNT DOES NOT MATCH. The guard that turns silent data
    # loss into a visible refusal: whatever goes wrong between the form and the
    # database, writing FEWER steps than were posted must never look like
    # success. It is cheap, it does not care WHY the numbers differ, and it would
    # have caught the contiguous-walk bug above on its first occurrence instead
    # of destroying somebody's copy.
    kept = [i for i in posted
            if (form.get(f'delete_{i}') or '') != '1' and _content(i)]

    # CHECKED BEFORE THE WRITE, so "nothing was written" is TRUE when it says so.
    # This is where the contiguous-walk bug lived: steps present on the screen
    # never reached `rows` at all.
    if len(rows) < len(kept):
        return _campaign_view(
            request, campaign_id, steps=_posted_steps(rows),
            seq_msg=(f'REJECTED: {len(kept)} step(s) were on the screen but only '
                     f'{len(rows)} reached the save. NOTHING was written. The '
                     f'mismatch itself is the bug - report it rather than '
                     f'retrying.'))
    try:
        saved = _drip_mod.save_steps(campaign_id, rows)
        # AND AFTER. A drop inside save_steps is a different fault, and this
        # message does NOT claim a rollback - save_steps commits, so by here the
        # sequence may be partial and saying otherwise would be the same kind of
        # false report this guard exists to prevent.
        if len(saved) != len(rows):
            msg = (f'⚠️ WROTE {len(saved)} step(s) FROM {len(rows)} POSTED. The '
                   f'sequence on screen may now be incomplete - reload and check '
                   f'it before sending anything. This is a bug, not a refusal.')
            return RedirectResponse(
                f'/campaign/{campaign_id}?msg={urllib.parse.quote(msg)}#drip',
                status_code=303)
        msg = f'Sequence saved - {len(saved)} step(s).'
    except _drip_mod.BadSequence as exc:
        # REFUSED WHOLE, AND THE TYPED COPY COMES BACK. A half-saved sequence is
        # worse than none: the schedule would be computed from steps nobody
        # approved. But a refusal that also throws away the draft turns a
        # correctable mistake into lost work - the operator retypes the step to
        # find out what was wrong with it.
        return _campaign_view(request, campaign_id, steps=_posted_steps(rows),
                              seq_msg=f'REJECTED: {exc}')
    return RedirectResponse(
        f'/campaign/{campaign_id}?msg={urllib.parse.quote(msg)}#drip',
        status_code=303)


@router.get('/campaign/{campaign_id}', response_class=HTMLResponse)
def campaign_page(request: Request, campaign_id: str, msg: str = '',
                  stop_confirm: str = ''):
    return _campaign_view(request, campaign_id, msg=msg,
                          stop_confirm=bool(stop_confirm))


def _campaign_view(request: Request, campaign_id: str, msg: str = '',
                   seq_msg: str = '', steps=None, stop_confirm: bool = False):
    """
    The campaign screen. `steps` overrides the saved sequence with POSTED rows.

    ⚠️ A REFUSED SAVE RE-RENDERS WHAT WAS TYPED. It used to redirect, which
    re-read the sequence from the database - so every refusal DESTROYED the copy
    that caused it. The operator saw a step they had just written disappear, and
    the explanation scrolled off the top of the page. Refusing is right; losing
    the work while refusing is the same silent-loss shape as the save that wrote
    fewer rows than were posted.

    `seq_msg` renders INSIDE the sequence card, because the redirect target is
    #drip and the page banner sits ~90 lines above it - a message the browser
    scrolls past is a message nobody reads.
    """
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
    _steps = _drip_mod.steps(campaign_id) if steps is None else steps
    # SENDING PACE, and the backlog it creates. Derived on every render from the
    # same query the sender runs, so the number on screen cannot drift from what
    # actually goes out.
    pace = _drip_mod.held(campaign_id) if camp['type'] == 'drip' else {}
    # EVERY drip, running or not, so a stopped one can still be chosen - and the
    # screen says which are stopped, because drip_for() refuses to route into a
    # stopped drip and that would otherwise look like the setting not working.

    # WHO FEEDS THIS DRIP - the relationship read from the other end, so the
    # wiring is auditable from both screens rather than only from the call side.
    # ⚠️ "FED BY" IS GONE BECAUSE NOTHING FEEDS A DRIP. Its successor is the
    # GATE: which statuses this drip accepts, how many leads hold each, and
    # which other running drips accept the same status.
    is_drip = camp['type'] == 'drip'
    gate_counts = _drip_mod.gate_counts() if is_drip else {}
    overlaps = _drip_mod.overlaps(campaign_id) if is_drip else []
    # ⚠️ THE RUNNING DRIPS AND WHAT THEY ACCEPT, for the FOLLOW-UP note on a CALL
    # campaign's page. The default_drip_id selector used to be there. It is gone
    # because nothing wires a drip any more - but a screen that simply LOSES a
    # control teaches nobody why; it just costs somebody an hour looking for it.
    running_drips = [c for c in campaigns.list_all()
                     if c['type'] == 'drip' and c['is_running']]
    drip_lead_counts = _drip_mod.qualifying_counts()
    # The chosen one, resolved here so the template can say "stopped" without
    # searching the list itself.

    gap_avg = ((camp['email_gap_min_seconds'] + camp['email_gap_max_seconds'])
               / 2.0) or 1
    # The REAL rate is whichever binds first: the gap or the hourly cap. Showing
    # the gap's rate alone would overstate it fourfold at these defaults, which
    # is the kind of number that gets trusted and then blamed.
    emails_per_hour = min(round(3600.0 / gap_avg, 1),
                          float(camp['email_hourly_cap']))
    hours_to_clear = (round(pace.get('scheduled', 0) / emails_per_hour, 1)
                      if emails_per_hour and pace.get('scheduled') else 0)
    # ⚠️ HOW MANY LEADS FALL BACK TO THE OPERATOR'S HOURS. leads.timezone is
    # nullable since the email-only import, and a fallback nobody can see is a
    # silent behaviour change for exactly the leads a drip holds most of.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # coalesce, because a CALL campaign has no accepted_statuses and
            # `= ANY(NULL)` is NULL rather than false - which is an error here,
            # not an empty count.
            cur.execute("""SELECT count(*) AS n FROM leads l
                            WHERE l.timezone IS NULL
                              AND l.status = ANY(coalesce(
                                    (SELECT accepted_statuses
                                       FROM campaign_configs
                                      WHERE campaign_id = %s), '{}'))""",
                        (campaign_id,))
            tz_fallback = cur.fetchone()['n']
    return templates.TemplateResponse(request, 'campaign.html', {
        'hdr': hdr, 'c': camp, 'queue': q, 'msg': msg, 'seq_msg': seq_msg,
        'gate_counts': gate_counts, 'overlaps': overlaps,
        'running_drips': running_drips, 'drip_lead_counts': drip_lead_counts,
        'statuses': STATUSES, 'caution': _drip_mod.CAUTION_STATUSES,
        'stop_confirm': stop_confirm,
        'pace': pace, 'emails_per_hour': emails_per_hour,
        'hours_to_clear': hours_to_clear, 'tz_fallback': tz_fallback,
        'operator_tz': cfg.OPERATOR_TIMEZONE,
        'per_hour': per_hour, 'remaining': remaining,
        # The ladders, and how many rungs of each can actually fire. A rung
        # only applies if another attempt follows it, so a four-rung ladder
        # under max attempts 4 has a fourth rung that is decoration.
        'retry_rows': RETRY_ROWS,
        # THE SEQUENCE, and a live preview of each step rendered against the
        # same sample lead the email-1 preview uses - so what you see is what
        # drafts.render() will actually produce.
        'drip_steps': _steps,
        # RENDERED ON LOAD as well as on every edit, so the preview is correct
        # before anything is typed rather than empty until the first keystroke.
        'step_previews': {
            st['position']: {
                'subject': drafts_mod.render(
                    st['subject'], drafts_mod.values_for(pv_lead, camp)),
                'body': drafts_mod.render(
                    st['body'], drafts_mod.values_for(pv_lead, camp))}
            for st in _steps},
        'source_split': _drip_mod.source_split(campaign_id),
        'preview_candidates': drafts_mod.preview_candidates(campaign_id),
        'sample_id': drafts_mod.SAMPLE_ID,
        'sample_lead': drafts_mod.SAMPLE_LEAD,
        'placeholders': drafts_mod.PLACEHOLDERS,
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


@router.get('/drips', response_class=HTMLResponse)
def drips_page(request: Request, drip: str = '', msg: str = ''):
    """
    THE DRIP AREA - separate from calls, not a filter on the shared leads list.

    ⚠️ WHY A SEPARATE PAGE RATHER THAN COLUMNS ON /leads. The two views want
    different facts about the same firm. A call needs phone, attempts and scores;
    a sequence needs step sent, clicks by step and next due. Putting both on one
    table gave 17 columns and a row that wrapped - and the shared list is the
    screen read first, so it is the one that must stay scannable.

    Lead DETAIL stays whole: calls and emails in ONE timeline, because that is the
    one place the entire relationship belongs together.
    """
    cfg = _cfg()
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
    drips = [c for c in campaigns.list_all() if c['type'] == 'drip']
    if not drips:
        return templates.TemplateResponse(request, 'drips.html', {
            'hdr': hdr, 'drips': [], 'msg': msg})
    # The picked drip, or the first. An id that is not a drip falls back rather
    # than 404ing: a stale bookmark should land somewhere useful.
    chosen = next((c for c in drips if str(c['campaign_id']) == str(drip)),
                  drips[0])
    cid = chosen['campaign_id']
    stats = _drip_mod.step_stats(cid)
    pv_lead, pv_real = drafts_mod.preview_lead(cid)
    return templates.TemplateResponse(request, 'drips.html', {
        'hdr': hdr, 'drips': drips, 'c': chosen, 'msg': msg,
        'step_stats': stats,
        'any_sent': any(r['sent'] for r in stats),
        'pace': _drip_mod.held(cid),
        'roster_count': len(_drip_mod.roster(cid)),
        # WHO FEEDS IT. The wiring lives on the CALL campaign, so without this
        # the drip area cannot answer "where do these leads come from" - and it
        # is the screen the drip work actually happens on.
        'overlaps': _drip_mod.overlaps(cid),
        'drip_lead_counts': _drip_mod.qualifying_counts(),
        # ⚠️ THE SEQUENCE EDITOR IS AN INCLUDE, so it needs exactly the context
        # the campaign page gives it. Anything missing renders as empty rather
        # than erroring, which is how a template silently loses a control.
        'drip_steps': _drip_mod.steps(cid),
        'step_previews': {
            st['position']: {
                'subject': drafts_mod.render(
                    st['subject'], drafts_mod.values_for(pv_lead, chosen)),
                'body': drafts_mod.render(
                    st['body'], drafts_mod.values_for(pv_lead, chosen))}
            for st in _drip_mod.steps(cid)},
        'source_split': _drip_mod.source_split(cid),
        'preview_candidates': drafts_mod.preview_candidates(cid),
        'sample_id': drafts_mod.SAMPLE_ID,
        'sample_lead': drafts_mod.SAMPLE_LEAD,
        'placeholders': drafts_mod.PLACEHOLDERS,
        'seq_msg': ''})


@router.get('/drips/{campaign_id}/leads', response_class=HTMLResponse)
def drip_leads_page(request: Request, campaign_id: str, msg: str = '',
                    confirm_send: str = ''):
    """
    THE ROSTER, on its own page.

    ⚠️ SPLIT FROM THE CONFIG PAGE because that screen had stacked the metrics, the
    per-step table, the roster and the sequence editor - four things with four
    different jobs, and the roster is the only one that is about individual firms.
    The per-step table stays on config: it is about the SEQUENCE, not the leads.
    """
    cfg = _cfg()
    with db.get_conn() as conn:
        hdr = _header(conn, cfg)
    camp = campaigns.get(campaign_id)
    if camp is None or camp['type'] != 'drip':
        return HTMLResponse('<p>no such drip</p>', status_code=404)
    return templates.TemplateResponse(request, 'drip_leads.html', {
        'hdr': hdr, 'c': camp, 'msg': msg,
        'roster': _drip_mod.roster(campaign_id),
        'pace': _drip_mod.held(campaign_id),
        'operator_tz': cfg.OPERATOR_TIMEZONE,
        # The status dropdown lives in the pill, so it needs the same list and the
        # same warning the lead page uses - one control, two places, one meaning.
        'manual_statuses': MANUAL_STATUSES,
        'status_sends': _drip_mod.sending_statuses(),
        'confirm_send': confirm_send})


@router.post('/drips/{campaign_id}/leads/{lead_id}/send-now')
def drip_send_now(campaign_id: str, lead_id: str, step_id: str = Form(...),
                  confirm: str = Form('')):
    """
    Send one step to one lead NOW, overriding the schedule.

    ⚠️ IT SKIPS THE TIMING AND NOTHING ELSE. The step delay, business hours and
    the pace queue all live in SELECTION; every exclusion - do-not-send,
    suppression, no address, replied, the campaign switch, the dev guard - lives
    inside send_step's transaction. So this builds the row selection would have
    built and calls the SAME send_step: not a second path with its own copy of the
    guards, which is the kind that eventually forgets one.

    Confirmed first, because it puts mail on the wire.
    """
    if confirm != 'yes':
        return RedirectResponse(
            f'/drips/{campaign_id}/leads?confirm_send={lead_id}:{step_id}',
            status_code=303)
    row = _drip_mod.row_for_send(campaign_id, lead_id, step_id)
    if row is None:
        msg = ('REJECTED: that step is not on this drip, or the lead has no '
               'address - nothing was sent.')
    else:
        r = _drip_mod.send_step(_cfg(), row)
        msg = (f'Sent step {row["position"]} to {row["company"]} now.'
               if r['sent'] else f'REFUSED: {r["detail"]}')
    return RedirectResponse(
        f'/drips/{campaign_id}/leads?msg={urllib.parse.quote(msg)}',
        status_code=303)


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
def campaign_save(campaign_id: str, name: str = Form(...), notes: str = Form(None),
                  agent_l1_version: int = Form(None),
                  sender_email: str = Form(...), sender_name: str = Form(...),
                  sender_company_line: str = Form(...),
                  daily_cap: int = Form(None),
                  max_concurrent: int = Form(None),
                  dial_interval_min: int = Form(None),
                  dial_interval_max: int = Form(None),
                  max_attempts: int = Form(None),
                  retry_busy: str = Form(None),
                  retry_no_answer: str = Form(None),
                  retry_voicemail: str = Form(None),
                  email_gap_min_seconds: int = Form(None),
                  email_gap_max_seconds: int = Form(None),
                  email_hourly_cap: int = Form(None),
                  email_daily_cap: int = Form(None),
                  accepted_statuses: list = Form(None)):
    """
    ⚠️ THE CALL FIELDS ARE OPTIONAL BECAUSE A DRIP SCREEN DOES NOT RENDER THEM.

    They were `Form(...)` - required - and the drip campaign screen shows no cap,
    no spacing and no prompt version, so SAVING A DRIP'S NAME OR SENDER WAS A 422
    for as long as drip campaigns have existed. Nobody hit it because the useful
    control on that screen is the sequence form, which posts elsewhere.

    Optional does NOT mean defaulted: a missing field is not written at all, and
    for a CALL campaign the fields the screen does render are checked below and
    refused if absent. Defaulting a missing cap to anything would let a partial
    post silently rewrite a live campaign's pacing.
    """
    camp = campaigns.get(campaign_id)
    if camp is None:
        return HTMLResponse('<p>no such campaign</p>', status_code=404)

    def _refuse(why):
        return RedirectResponse(
            f'/campaign/{campaign_id}?msg={urllib.parse.quote("REJECTED: " + why)}',
            status_code=303)

    # WHAT THIS TYPE'S SCREEN RENDERS IS WHAT THIS TYPE MUST POST. Absent means
    # the form was incomplete, which is a bug worth refusing rather than a
    # value worth guessing.
    # ⚠️ THE GATE. An EMPTY list is a real answer - a drip that accepts nobody -
    # and None means the field was not on the form at all. The form posts a
    # hidden marker so "all unticked" arrives as [] rather than as absent, or
    # clearing the last status would be indistinguishable from not submitting it.
    if accepted_statuses is not None:
        # The hidden marker posts '' so that "all unticked" arrives as a list
        # rather than as an absent field. It is a marker, not a status.
        accepted_statuses = [x for x in accepted_statuses if x]
        bad = [x for x in accepted_statuses if x not in STATUSES]
        if bad:
            return _refuse(f'not a lead status: {", ".join(bad)}')
    if camp['type'] == 'call':
        missing = [n for n, v in (('prompt version', agent_l1_version),
                                  ('daily cap', daily_cap),
                                  ('calls at a time', max_concurrent),
                                  ('gap min', dial_interval_min),
                                  ('gap max', dial_interval_max)) if v is None]
        if missing:
            return _refuse(f'the form did not post {", ".join(missing)}. '
                           f'Nothing was saved.')
        if dial_interval_min > dial_interval_max:
            return _refuse('gap min cannot exceed gap max')
    else:
        missing = [n for n, v in (('send gap min', email_gap_min_seconds),
                                  ('send gap max', email_gap_max_seconds),
                                  ('hourly cap', email_hourly_cap),
                                  ('daily cap', email_daily_cap)) if v is None]
        if missing:
            return _refuse(f'the form did not post {", ".join(missing)}. '
                           f'Nothing was saved.')
        # ⚠️ SAME MESSAGE AS THE DIAL GAP, because it is the same mistake. The
        # database CHECK refuses it too; this is so the operator reads a
        # sentence instead of a constraint name.
        if email_gap_min_seconds > email_gap_max_seconds:
            return _refuse('send gap min cannot exceed gap max')
        if email_daily_cap > 250:
            return _refuse(f'{email_daily_cap} a day is above the 250 ceiling. '
                           f'A new sending domain earns volume; it cannot be '
                           f'given it.')
        if email_hourly_cap * 8 > email_daily_cap:
            # NOT A REFUSAL ELSEWHERE - just arithmetic nobody should have to
            # do. An hourly cap that cannot be reached inside a working day is
            # a control that does nothing, the same class as step 1's timing on
            # a call-only drip.
            pass
    # The sender is a choice from a list. A typed address that Brevo has never
    # heard of is the config-that-cannot-send this replaced a text field to stop.
    if not senders_mod.is_allowed(_cfg(), sender_email):
        return RedirectResponse(
            f'/campaign/{campaign_id}?msg={urllib.parse.quote(f"REJECTED: {sender_email} is not an offered or verified sender")}',
            status_code=303)
    try:
        # ONLY WHAT WAS POSTED. A None here means the field is not on this
        # type's screen, and writing it would overwrite a real value with a
        # guess.
        # ⚠️ notes IS ONLY WRITTEN WHEN POSTED. It was Form(''), so a config
        # POST that omitted it BLANKED it - which is how C1's notes were lost
        # while verifying an unrelated selector. Same rule as everything else on
        # this handler: absent means untouched, never "set to the default".
        fields = {'name': name,
                  'sender_email': sender_email, 'sender_name': sender_name,
                  'sender_company_line': sender_company_line}
        if notes is not None:
            fields['notes'] = notes
        for k, v in (('agent_l1_version', agent_l1_version),
                     ('daily_cap', daily_cap),
                     ('max_concurrent', max_concurrent),
                     ('dial_interval_min', dial_interval_min),
                     ('dial_interval_max', dial_interval_max),
                     ('email_gap_min_seconds', email_gap_min_seconds),
                     ('email_gap_max_seconds', email_gap_max_seconds),
                     ('email_hourly_cap', email_hourly_cap),
                     ('email_daily_cap', email_daily_cap)):
            if v is not None:
                fields[k] = v
        if accepted_statuses is not None:
            fields['accepted_statuses'] = accepted_statuses
        if camp['type'] == 'call':
            fields.update(_retry_fields(max_attempts, retry_busy,
                                        retry_no_answer, retry_voicemail))
        campaigns.update(campaign_id, **fields)
        msg = (f'saved - prompt v{agent_l1_version} is now live'
               if camp['type'] == 'call' else
               f'saved - {email_hourly_cap}/hour, {email_daily_cap}/day, '
               f'{email_gap_min_seconds}-{email_gap_max_seconds}s apart')
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
def campaign_stop(campaign_id: str, confirm: str = Form('')):
    """
    Stop a campaign.

    ⚠️ STOPPING A DRIP WITH LEADS IN IT IS NEVER SILENT. Only a RUNNING drip
    sends, so stopping one means every lead currently qualifying receives
    nothing - mid-sequence, with no error and no queue to look at.

    The confirmation used to name the CALL CAMPAIGNS that fed it. Nothing feeds a
    drip now, so it names the count of leads that qualify right now instead:
    the same consequence, measured by the mechanism that actually decides it.

    Nothing is lost by stopping - the gate is unchanged and the same leads
    qualify again the moment it restarts. There is no delete route for a
    campaign, so stopping is the only way to break the chain.
    """
    row = campaigns.get(campaign_id)
    if row is None:
        return HTMLResponse('<p>no such campaign</p>', status_code=404)
    if row['type'] == 'drip' and confirm != 'yes':
        if _drip_mod.roster(campaign_id, limit=1):
            return RedirectResponse(
                f'/campaign/{campaign_id}?stop_confirm=1', status_code=303)
    row = campaigns.stop(campaign_id)
    name = row['name'] if row else 'campaign'
    what = ('stopped - steps will not send, and leads finishing email 1 will '
            'join NO drip' if row and row['type'] == 'drip'
            else 'stopped - nothing dials')
    return RedirectResponse(
        f'/campaign/{campaign_id}?msg={urllib.parse.quote(name + " " + what)}',
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
                            -- NAMED FIRST among the email cases, because it is
                            -- the one nothing else would ever tell you about.
                            WHEN l.emailed_at IS NOT NULL
                                 AND NOT EXISTS (
                                       SELECT 1 FROM campaign_configs dc
                                        WHERE dc.type = 'drip' AND dc.is_running
                                          AND l.status = ANY(dc.accepted_statuses))
                                 AND l.replied_at IS NULL
                                 THEN 'emailed, on no drip - the sequence stalled'
                            WHEN l.dm_email IS NOT NULL
                                 AND l.dm_email_confirmed IS NOT TRUE
                                 AND l.lead_source <> 'import'
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
        'needs_you': needs, 'sent': sent, 'digest_hour': 18,
        # ⚠️ THE PROACTIVE HALF of "emailed and on no drip". That net finds leads
        # ALREADY past email 1; this finds the CONFIGURATION that will send the
        # next one nowhere, which is observable before anybody is affected and
        # was completely silent.
        'wiring': campaigns.wiring_problems()})


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
    cols = ['company', 'phone_e164', 'timezone', 'city', 'state',
            'has_confirmed_email',
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
