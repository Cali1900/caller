#!/usr/bin/env bash
# Weekly off-droplet backup of the SUPPRESSION list.
#
# Suppression is the only table where losing a row causes real-world harm:
# it means calling somebody who told us to stop. Everything else can be
# rebuilt from a CSV.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T caller-api python -c "
import json
from api import backup
from api.config import load_config
print(json.dumps(backup.run(load_config()), indent=2))
"
