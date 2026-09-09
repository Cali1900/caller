#!/usr/bin/env bash
# IS IT SAFE TO EDIT api/ RIGHT NOW?
#
# ⚠️ THE THIRD DISTINCT WAY THIS TOOL HAS COST REAL TIME.
#
#   1. killed mid-run, leaving a break LIVE in api/ (fixed: state dir + journal
#      + signal traps + --check/--recover)
#   2. `git add -A` during a pass, committing whichever safety was off (fixed:
#      .githooks/pre-commit)
#   3. EDITING api/ during a pass. A pass snapshots its targets at the start of
#      each chunk and restores them at the end, so any edit made while it runs is
#      SILENTLY REVERTED at the next chunk boundary. On 2026-09-09 that reverted
#      a rewrite of api/web.py AND api/templates/campaign.html, and the loss was
#      invisible: the files still parsed, the suite still passed, and a deploy
#      shipped a page that had never contained the change.
#
# The pre-commit hook covers commits and scripts/test.sh covers test runs.
# EDITING was the hole, and an edit cannot be intercepted - so this is the check
# to run before starting, and what the hook and test.sh point at.
set -uo pipefail
cd "$(dirname "$0")/.."

LOCK=.break_pass_state.lock
busy=0

if [[ -d .break_pass_state ]]; then
  echo "⛔ A BREAK PASS IS RUNNING OR WAS KILLED - DO NOT EDIT api/."
  echo "   It restores its targets at every chunk boundary, so an edit made now"
  echo "   is reverted silently and you will not be told."
  [[ -f .break_pass_state/ACTIVE ]] && \
    echo "   currently applied: $(cat .break_pass_state/ACTIVE)"
  busy=1
elif [[ -e "$LOCK" ]] && command -v flock >/dev/null 2>&1 \
     && ! flock -n "$LOCK" true 2>/dev/null; then
  echo "⛔ A BREAK PASS HOLDS THE LOCK - DO NOT EDIT api/."
  busy=1
fi

if [[ $busy == 1 ]]; then
  echo
  echo "   ./scripts/break_pass_all.sh --status   how far it has got"
  echo "   Wait for it, or stop it BETWEEN chunks (when no break is applied)"
  echo "   and check with ./scripts/break_pass.sh --check before editing."
  exit 1
fi

# Not running. Say so, and prove api/ is actually clean rather than merely
# unlocked - a completed run that failed to restore leaves no state at all.
echo "✓ no break pass running - api/ is safe to edit"
python3 scripts/breaks_anchor_check.py
