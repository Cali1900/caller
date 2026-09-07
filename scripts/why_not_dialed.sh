#!/usr/bin/env bash
# "Why did nothing dial?" answered per lead, in one place.
#   ./scripts/why_not_dialed.sh <lead_id>
set -euo pipefail
cd "$(dirname "$0")/.."
LEAD="${1:?usage: why_not_dialed.sh <lead_id>}"
docker compose exec -T caller-api python -c "
from api import db, windows, campaigns
from api.config import load_config
cfg = load_config(); date = campaigns.campaign_date(cfg)
with db.get_conn() as c:
    with c.cursor() as cur:
        cur.execute(windows.window_debug_sql(), ('$LEAD',))
        r = cur.fetchone()
        if not r: print('no such lead'); raise SystemExit
        for k, v in r.items(): print(f'  {k:22s}: {v}')
        cur.execute('''SELECT cl.source, cl.dialed_at, c.started_at, c.paused,
                              c.daily_cap,
                              (SELECT count(*) FROM campaign_leads x
                                WHERE x.campaign_date=c.campaign_date
                                  AND x.dialed_at IS NOT NULL) AS dialed_today
                         FROM campaign_leads cl JOIN campaigns c
                           ON c.campaign_date=cl.campaign_date
                        WHERE cl.lead_id=%s AND cl.campaign_date=%s''',
                    ('$LEAD', date))
        cr = cur.fetchone()
        print('  --- campaign', date, '---')
        if not cr: print('  NOT ENROLLED in today\'s campaign')
        else:
            for k, v in cr.items(): print(f'  {k:22s}: {v}')
        cur.execute('''SELECT outcome, detail, created_at FROM dial_audit
                        WHERE lead_id=%s ORDER BY id DESC LIMIT 5''', ('$LEAD',))
        print('  --- last dial_audit ---')
        for a in cur.fetchall(): print(f'  {a[\"created_at\"]}  {a[\"outcome\"]}  {a[\"detail\"] or \"\"}')
"
