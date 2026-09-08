"""
THE FUNNEL — where leads stop, and which step is worst.

A COHORT FUNNEL, NOT AN EVENT FUNNEL. That is the important choice here, and
it is what makes week-over-week comparison mean anything.

A date range selects the leads FIRST DIALED in that window, then follows that
same set all the way down - so "of the leads I started calling last week, how
far did they get". An event funnel would count a call from Monday and an email
from Thursday in the same column and produce percentages that cannot be read:
a good week's emails against a bad week's calls.

The cost is that a recent range looks worse than it is - leads dialed
yesterday have not had time to click. The page says so rather than letting the
number be misread.

Filtering by PROMPT VERSION is the point of the whole screen: it answers "did
that script change move a specific step", which is otherwise guesswork.
"""

from api import db

# Each step: (key, label, denominator_key or None for the first)
STEPS = (
    ('in_queue',    'in queue',        None),
    ('dialed',      'dialed',          'in_queue'),
    ('reached',     'reached a human', 'dialed'),
    ('named',       'gave a name',     'reached'),
    ('emailed_cap', 'gave an email',   'named'),
    ('emailed',     'emailed',         'emailed_cap'),
    ('clicked',     'clicked',         'emailed'),
    ('replied',     'replied',         'emailed'),
    ('demo',        'demo booked',     'emailed'),
)


def _where(campaign_id, agent_version, date_from, date_to, status=''):
    """Filters on the LEAD COHORT, not on individual events."""
    where, params = ['1=1'], {}
    if campaign_id:
        where.append('l.campaign_id = %(cid)s')
        params['cid'] = campaign_id
    if date_from:
        where.append('l.first_dialed_at >= %(dfrom)s::date')
        params['dfrom'] = date_from
    if date_to:
        where.append('l.first_dialed_at < (%(dto)s::date + 1)')
        params['dto'] = date_to
    if status:
        where.append('l.status = %(status)s')
        params['status'] = status
    if agent_version not in (None, ''):
        # A lead belongs to a version if any of its calls ran that version.
        where.append("""EXISTS (SELECT 1 FROM calls cv
                                 WHERE cv.lead_id = l.lead_id
                                   AND cv.agent_version = %(ver)s)""")
        params['ver'] = int(agent_version)
    return ' AND '.join(where), params


def counts(campaign_id='', agent_version='', date_from='', date_to='',
           status=''):
    where, params = _where(campaign_id, agent_version, date_from, date_to, status)
    sql = f"""
        SELECT
          count(*)                                                  AS in_queue,
          count(*) FILTER (WHERE l.first_dialed_at IS NOT NULL)     AS dialed,
          count(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM calls c WHERE c.lead_id = l.lead_id
               AND c.transcript IS NOT NULL AND c.transcript <> ''))AS reached,
          count(*) FILTER (WHERE coalesce(l.dm_name,'') <> '')      AS named,
          count(*) FILTER (WHERE coalesce(l.dm_email,'') <> '')     AS emailed_cap,
          count(*) FILTER (WHERE l.emailed_at IS NOT NULL)          AS emailed,
          count(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM email_clicks e WHERE e.lead_id = l.lead_id)) AS clicked,
          count(*) FILTER (WHERE l.replied_at IS NOT NULL)          AS replied,
          count(*) FILTER (WHERE l.status = 'demo_pending')         AS demo
        FROM leads l
       WHERE {where}"""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return dict(cur.fetchone())


def build(campaign_id='', agent_version='', date_from='', date_to='',
          status=''):
    """
    Rows of {key, label, n, pct, pct_of, drop_n, drop_pct, worst}.

    `pct` is of the PREVIOUS step, which is the number that says where people
    stop. A percentage of the top would make every late step look terrible and
    hide which one is actually leaking.
    """
    c = counts(campaign_id, agent_version, date_from, date_to, status)
    rows = []
    for key, label, denom_key in STEPS:
        n = c.get(key) or 0
        row = {'key': key, 'label': label, 'n': n, 'pct': None,
               'pct_of': denom_key, 'drop_n': None, 'drop_pct': None,
               'worst': False}
        if denom_key:
            d = c.get(denom_key) or 0
            row['pct'] = round(100.0 * n / d, 1) if d else None
            row['drop_n'] = d - n
            row['drop_pct'] = round(100.0 * (d - n) / d, 1) if d else None
        rows.append(row)

    # THE WORST STEP is the biggest proportional drop, and only among steps
    # that had something to lose. A step with a denominator of zero has not
    # failed - it has not been tested, and calling it the worst would point at
    # the wrong thing every time a funnel is young.
    MIN_DENOM = 3
    candidates = [r for r in rows
                  if r['drop_pct'] is not None
                  and (c.get(r['pct_of']) or 0) >= MIN_DENOM
                  and r['drop_n'] > 0
                  # replied/demo hang off `emailed` alongside clicked; they are
                  # outcomes, not a sequential leak, so they do not compete for
                  # "worst step".
                  and r['key'] not in ('replied', 'demo')]
    if candidates:
        worst = max(candidates, key=lambda r: r['drop_pct'])
        worst['worst'] = True
    return {'rows': rows, 'counts': c,
            'thin': (c.get('in_queue') or 0) < MIN_DENOM}


def versions_seen(campaign_id=''):
    """Prompt versions that actually placed calls, newest first."""
    where, params = ('l.campaign_id = %(cid)s', {'cid': campaign_id}) \
        if campaign_id else ('1=1', {})
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT DISTINCT c.agent_version AS v
                              FROM calls c JOIN leads l USING (lead_id)
                             WHERE {where} AND c.agent_version IS NOT NULL
                             ORDER BY v DESC""", params)
            return [r['v'] for r in cur.fetchall()]
