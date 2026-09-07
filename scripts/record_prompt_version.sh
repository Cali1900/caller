#!/usr/bin/env bash
# Snapshot the pinned Retell agent version into prompt_versions, WITH the
# model and prompt length. A score movement after a model swap is
# unattributable without both.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T caller-api python -c "
import json
from api import prompts
from api.config import load_config
print(json.dumps(prompts.snapshot(load_config(), changed_by='${USER:-operator}',
                 note='${1:-snapshot}'), indent=2, default=str))
"
