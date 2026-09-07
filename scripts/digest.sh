#!/usr/bin/env bash
# Build (and optionally send) the end-of-day digest.
#   ./scripts/digest.sh            # print it, do not send
#   ./scripts/digest.sh send       # send it (idempotent per day)
#   ./scripts/digest.sh send force # re-send
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-preview}"; FORCE="${2:-}"
docker compose exec -T caller-api python -c "
import json
from api import digest
from api.config import load_config
cfg = load_config()
if '$MODE' == 'send':
    print(json.dumps(digest.send(cfg, force=('$FORCE'=='force')), indent=2, default=str))
else:
    d = digest.build(cfg)
    print('SUBJECT:', d['subject']); print(); print(d['body'])
"
