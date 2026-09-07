#!/usr/bin/env bash
# Drive a day's campaign.
#   ./scripts/campaign.sh status
#   ./scripts/campaign.sh enroll        # carry-overs first, then fresh to cap
#   ./scripts/campaign.sh start         # NOTHING dials until this
#   ./scripts/campaign.sh pause | resume
#   ./scripts/campaign.sh rollover
#   ./scripts/campaign.sh cap 200
set -euo pipefail
cd "$(dirname "$0")/.."
CMD="${1:?usage: campaign.sh <status|enroll|start|pause|resume|rollover|cap N>}"
ARG="${2:-}"
docker compose exec -T caller-api python -c "
import json, sys
from api import campaigns
from api.config import load_config
cfg = load_config()
cmd, arg = '$CMD', '$ARG'
if cmd == 'cap':
    from api import db
    # ensure() FIRST: without a campaign row the UPDATE matches nothing and
    # reports success while changing nothing - the exact silent no-op this
    # codebase refuses everywhere else.
    campaigns.ensure(cfg)
    with db.get_conn() as c:
        with c.cursor() as cur:
            cur.execute('UPDATE campaigns SET daily_cap=%s WHERE campaign_date=%s',
                        (int(arg), campaigns.campaign_date(cfg)))
            if cur.rowcount != 1:
                sys.exit(f'FAILED: cap not applied (matched {cur.rowcount} rows)')
    print('cap set to', arg)
    r = campaigns.status(cfg)
else:
    r = getattr(campaigns, cmd)(cfg)
print(json.dumps(r, indent=2, default=str))
"
