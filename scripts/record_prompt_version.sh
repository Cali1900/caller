#!/usr/bin/env bash
# Snapshot a Retell agent version into prompt_versions, WITH the model and
# prompt length. A score movement after a model swap is unattributable
# without both.
#
# THE VERSION COMES FROM THE RUNNING CAMPAIGN, which is the only place that
# says which prompt is live. Pass --version=N to snapshot a different one
# deliberately; there is no env default, because a default here is a second
# answer to "what is live" and it is always the stale one.
#
#   ./scripts/record_prompt_version.sh "note"
#   ./scripts/record_prompt_version.sh "note" --version=9
set -euo pipefail
cd "$(dirname "$0")/.."
NOTE="${1:-snapshot}"
VERSION=""
for arg in "$@"; do
  case "$arg" in --version=*) VERSION="${arg#--version=}" ;; esac
done
docker compose exec -T caller-api python -c "
import json, sys
from api import prompts, campaigns
from api.config import load_config
want = '''${VERSION}'''.strip()
if want:
    version = int(want)
    where = 'passed on the command line'
else:
    camp = campaigns.running()
    if not camp:
        sys.exit('no campaign is running, so nothing says which version is '
                 'live. Start one, or pass --version=N.')
    version = camp['agent_l1_version']
    where = f'the running campaign {camp[\"name\"]!r}'
print(f'snapshotting agent version {version} (from {where})', file=sys.stderr)
print(json.dumps(prompts.snapshot(load_config(), version,
                 changed_by='${USER:-operator}',
                 note='${NOTE}'), indent=2, default=str))
"
