#!/usr/bin/env bash
# Score any unscored calls now (the worker also does this every 60s).
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T caller-api python -c "
import json
from api import scorer
from api.config import load_config
print(json.dumps(scorer.score_pending(load_config(), limit=${1:-25}), indent=2))
"
