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
op_tz = cfg.OPERATOR_TIMEZONE
with db.get_conn() as c:
    with c.cursor() as cur:
        cur.execute(windows.window_debug_sql(), ('$LEAD',))
        r = cur.fetchone()
        if not r: print('no such lead'); raise SystemExit
        for k, v in r.items(): print(f'  {k:22s}: {v}')
        # Campaigns are NAMED CONFIGURATIONS now. This used to read
        # campaign_leads and campaigns, both dropped in migration 013 - so the
        # tool for why-nothing-dialed crashed on the one question it exists to
        # answer. No double quotes in here: this whole block is inside a
        # shell-quoted python -c, and one would end the string.
        cur.execute('''SELECT c.name, c.is_running, c.daily_cap,
                              c.max_concurrent, c.agent_l1_version,
                              (SELECT count(*) FROM leads x
                                WHERE x.campaign_id = c.campaign_id
                                  AND x.first_dialed_at IS NOT NULL
                                  AND (x.first_dialed_at AT TIME ZONE %s)::date
                                      = (now() AT TIME ZONE %s)::date)
                                AS new_leads_dialed_today
                         FROM campaign_configs c
                         JOIN leads l ON l.campaign_id = c.campaign_id
                        WHERE l.lead_id = %s''',
                    (op_tz, op_tz, '$LEAD'))
        cr = cur.fetchone()
        print('  --- campaign ---')
        if not cr:
            print('  NOT ON ANY CAMPAIGN - assign it from the leads list')
        else:
            for k, v in cr.items(): print(f'  {k:22s}: {v}')
            if not cr['is_running']:
                print('  >> THE CAMPAIGN IS STOPPED - nothing dials until it is started at /campaigns')
            elif cr['new_leads_dialed_today'] >= cr['daily_cap']:
                print('  >> DAILY CAP REACHED for this campaign.')
        cur.execute('''SELECT outcome, detail, created_at FROM dial_audit
                        WHERE lead_id=%s ORDER BY id DESC LIMIT 5''', ('$LEAD',))
        print('  --- last dial_audit ---')
        for a in cur.fetchall(): print(f'  {a[\"created_at\"]}  {a[\"outcome\"]}  {a[\"detail\"] or \"\"}')
"
