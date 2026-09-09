#!/usr/bin/env bash
# Bring the stack up in the required order:
#   postgres -> migrations -> api + worker
#
# The ordering is the point. Migrations run BEFORE the app containers come
# up, so a container never boots against a schema it does not match.
set -euo pipefail
cd "$(dirname "$0")/.."

# --- THE PRE-COMMIT HOOK IS PER-CLONE, SO BOOTSTRAP IT HERE ---------------
# .githooks/pre-commit refuses to commit while a break pass is running. A break
# pass removes a guard from api/ on purpose, so `git add -A` during one commits
# whichever safety is currently off - and the pass restores the file seconds
# later, so the diff looks innocent afterwards. That is exactly how the
# DIAL_ALLOWLIST check reached origin/main as a bare `return` (aa0f9c8,
# restored in cc19c9c).
#
# core.hooksPath is LOCAL CONFIG, not something the repo can enforce, so a
# fresh clone has zero protection until this runs. Idempotent, and quiet unless
# it actually changes something.
if [[ -d .git && -x .githooks/pre-commit ]]; then
  if [[ "$(git config --get core.hooksPath || true)" != ".githooks" ]]; then
    git config core.hooksPath .githooks
    echo "==> core.hooksPath -> .githooks (pre-commit break-pass guard armed)"
  fi
fi

./scripts/migrate.sh
echo "==> starting caller-api and caller-worker"
docker compose up -d
docker compose ps
