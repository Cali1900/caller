#!/usr/bin/env bash
# Run the suite inside the caller-api image, so tests use exactly the
# dependency set the service runs with. The host python is externally
# managed (PEP 668) and is not polluted.
#
# Tests run against caller_test_db, never the database the containers use.
set -euo pipefail
cd "$(dirname "$0")/.."
# api/ is mounted, NOT taken from the baked image. Without this, editing a
# guard on the host would not reach the test run at all - the break pass would
# report a landed edit and a green suite, which is the exact failure the break
# pass exists to catch.
# --- ONE TEST RUN AT A TIME -----------------------------------------------
# Three concurrent runs against one test database produced hours of results
# that looked like code regressions: breaks reappearing after restore,
# deadlocked TRUNCATEs, failures moving between runs. HANDOFF.md and README.md
# both claimed this guard existed here from 2026-09-08; it did not - the flock
# was in break_pass.sh and only ever guarded passes against each other.
#
# TWO DIFFERENT COLLISIONS, TWO DIFFERENT CHECKS:
#
#   test vs test   an exclusive lock of our OWN. NOT break_pass's lock: this
#                  script is invoked BY the break pass, so sharing that lock
#                  would make the pass block on itself - the same shape as the
#                  pgrep guard that fired on its own command line and had to
#                  be replaced.
#
#   test vs pass   a pass removes a guard from api/ on purpose, one at a time.
#                  A test run started mid-pass reads a deliberately broken
#                  tree and shares the test database with it. break_pass.sh
#                  checks for test containers at STARTUP only, so this is the
#                  direction that was never covered.
#
# BREAK_PASS=1 means "the pass is calling me": it already holds its own lock
# and already checked for stray test containers before it started, so both
# checks are skipped rather than fired at the caller.
if [[ -z "${BREAK_PASS:-}" ]]; then
  exec 9>".test_run.lock"
  if ! flock -n 9; then
    echo "ANOTHER TEST RUN IS ALREADY IN FLIGHT - refusing." >&2
    echo "They share caller_test_db; two at once produces results that look" >&2
    echo "like code regressions and are not. Wait for it, or check for a" >&2
    echo "stray container: docker ps | grep caller-caller-api-run" >&2
    exit 1
  fi
  if [[ -d .break_pass_state ]]; then
    echo "A BREAK PASS IS RUNNING (or was killed) - refusing to run tests." >&2
    echo >&2
    echo "It removes a guard from api/ on purpose, one at a time, and shares" >&2
    echo "caller_test_db. A run started now reads a deliberately broken tree" >&2
    echo "and reports failures that are not regressions." >&2
    echo >&2
    echo "  ./scripts/break_pass_all.sh --status   what it is doing" >&2
    echo "  ./scripts/break_pass.sh --check        is a break live right now?" >&2
    echo "  ./scripts/guard_break_pass.sh          is it safe to EDIT api/?" >&2
    echo >&2
    echo "AND DO NOT EDIT api/ WHILE IT RUNS. The pass restores its targets at" >&2
    echo "every chunk boundary, so an edit made now is reverted silently." >&2
    exit 1
  fi
fi

# ⚠️ scripts/ IS MOUNTED READ-ONLY so a test can run a repo check rather than
# reimplementing it. test_no_dead_config.py shells out to
# scripts/check_dead_controls.py: one definition of "is this control wired", used
# by the suite and by the pre-commit hook, because two copies of a check disagree
# eventually and the one nobody runs is the one that rots.
docker compose run --rm --no-deps \
  -v "$(pwd)/api:/app/api" \
  -v "$(pwd)/tests:/app/tests" \
  -v "$(pwd)/migrations:/app/migrations" \
  -v "$(pwd)/scripts:/app/scripts:ro" \
  caller-api python -m pytest tests/ "$@"
