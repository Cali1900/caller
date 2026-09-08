"""
PIPELINE FORECAST — what the queue is worth, weighted by how far it has got.

    monthly value  = demands_per_month x price_per_demand
    weighted value = monthly value x P(stage)

⚠️ EVERY NUMBER HERE RESTS ON A RECEPTIONIST'S ESTIMATE. She was asked how many
demands the firm sends in a month and answered from memory, in passing, to a
stranger. That is directional and it is not a contract, and any screen showing
these totals has to say so - a forecast presented as a figure someone can bank
is worse than no forecast, because it gets repeated.

TWO RULES THAT KEEP IT HONEST:

  1. NULL demands_per_month CONTRIBUTES NOTHING and is counted separately. She
     did not answer; that is not zero. Treating it as zero would quietly say
     "this firm sends no demands", and the totals would drift down every time
     someone declined to answer rather than reflecting anything real.

  2. A STAGE WITH NO WEIGHT CONTRIBUTES NOTHING. A lead we have not emailed has
     no forecastable value - not a small one.
"""

from api import db

# How far a lead has got, in the order that decides which weight applies.
# demo_booked beats engaged beats emailed: a lead that clicked AND booked is
# counted once, at the strongest stage it reached.
STAGE_SQL = """
    CASE WHEN l.status = 'demo_pending'                       THEN 'demo_booked'
         WHEN l.replied_at IS NOT NULL
           OR EXISTS (SELECT 1 FROM email_clicks e
                       WHERE e.lead_id = l.lead_id)           THEN 'engaged'
         WHEN l.emailed_at IS NOT NULL                        THEN 'emailed'
         ELSE 'unweighted' END
"""

WEIGHT_COL = {'demo_booked': 'p_demo_booked',
              'engaged': 'p_engaged',
              'emailed': 'p_emailed'}

LABEL = {'demo_booked': 'demo booked', 'engaged': 'engaged (clicked or replied)',
         'emailed': 'emailed', 'unweighted': 'not yet emailed'}


def build(campaign_id=''):
    """
    {'rows': [...], 'total_monthly', 'total_weighted', 'no_volume', 'price'}

    One row per stage: lead count, how many of those gave a volume, the
    unweighted monthly value and the weighted one.
    """
    where, params = ('l.campaign_id = %(cid)s', {'cid': campaign_id}) \
        if campaign_id else ('1=1', {})
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT ({STAGE_SQL.strip()}) AS stage,
                       count(*)                                     AS leads,
                       count(*) FILTER (
                           WHERE l.demands_per_month IS NOT NULL)    AS with_volume,
                       COALESCE(SUM(l.demands_per_month
                                    * c.price_per_demand), 0)        AS monthly,
                       c.price_per_demand                            AS price,
                       c.p_demo_booked, c.p_engaged, c.p_emailed
                  FROM leads l
                  JOIN campaign_configs c ON c.campaign_id = l.campaign_id
                 WHERE {where}
                 GROUP BY 1, c.price_per_demand, c.p_demo_booked,
                          c.p_engaged, c.p_emailed""", params)
            raw = [dict(r) for r in cur.fetchall()]

    rows, total_m, total_w = [], 0.0, 0.0
    for r in raw:
        stage = r['stage']
        weight = float(r[WEIGHT_COL[stage]]) if stage in WEIGHT_COL else 0.0
        monthly = float(r['monthly'] or 0)
        weighted = monthly * weight
        total_m += monthly
        total_w += weighted
        rows.append({'stage': stage, 'label': LABEL[stage],
                     'leads': r['leads'], 'with_volume': r['with_volume'],
                     # Visible on every row: a total built from six of thirty
                     # leads is a different claim from one built from thirty.
                     'no_volume': r['leads'] - r['with_volume'],
                     'weight': weight, 'monthly': monthly, 'weighted': weighted})

    order = ['demo_booked', 'engaged', 'emailed', 'unweighted']
    rows.sort(key=lambda r: order.index(r['stage']))
    return {'rows': rows,
            'total_monthly': total_m, 'total_weighted': total_w,
            'no_volume': sum(r['no_volume'] for r in rows),
            'with_volume': sum(r['with_volume'] for r in rows),
            'price': float(raw[0]['price']) if raw else None}
