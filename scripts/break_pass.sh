#!/usr/bin/env bash
# BREAK PASS - prove the guard tests are not decoration.
#
# For each guard in scripts/breaks/: remove it, confirm THE EDIT ACTUALLY
# LANDED, run its named test, require that test to go RED, then restore and
# verify the restore byte for byte.
#
# "A break that did not land is not a break." The edit is verified by content
# hash and the removed lines are printed BEFORE any test result is read, so a
# silently-failed patch can never be misread as "the guard is covered".
#
# ---------------------------------------------------------------------------
# ⚠️  THIS SCRIPT MUTATES REAL SOURCE FILES.
#
# It has twice been killed mid-run and left a break LIVE in api/ - once with
# `raise` inside the scorer's except, once with the ALLOWLIST CHECK DELETED,
# which is the one guard that stops us dialing strangers. A tool that silently
# leaves the safety off is worse than no tool.
#
# So the originals are not held in a temp dir that dies with the process. They
# are copied to a FIXED, PREDICTABLE path (.break_pass_state/) together with a
# manifest of their hashes, BEFORE anything is edited:
#
#   * traps on EXIT INT TERM HUP restore and verify (covers Ctrl-C, `timeout`,
#     and the 10-minute harness cap, none of which fire a bare EXIT trap alone)
#   * SIGKILL cannot be trapped, so the state directory SURVIVES on purpose:
#     the next run - or `--recover`, or `--check` - finds it and restores from
#     it before doing anything else, and REFUSES to proceed until clean
#   * every restore is verified by md5 against the manifest, never assumed
#
# Restores copy the original file back wholesale. They never string-replace
# NEW with OLD: NEW is often a bare `return`, which matches somewhere earlier
# in the file and silently corrupts it.
# ---------------------------------------------------------------------------
#
# Requiring the EXPECTED test to fail matters as much as requiring redness:
# a guard whose removal breaks some unrelated test is still untested. By
# default only that named test is run per break (seconds, not minutes); the
# full suite is run GREEN before and after, so a break that leaks is still
# caught. --full runs the whole suite per break instead.
#
# NOTE: tests run in the container with api/ MOUNTED (see scripts/test.sh).
# If api/ came from the baked image, every edit here would land on the host
# and change nothing in the test run - a green suite that proves nothing.

set -uo pipefail
cd "$(dirname "$0")/.."

STATE=.break_pass_state
ORIG="$STATE/originals"
MANIFEST="$STATE/MANIFEST"
ACTIVE="$STATE/ACTIVE"

MODE=run
FULL=0
ONLY=""
for arg in "$@"; do
  case "$arg" in
    --check)    MODE=check ;;
    --recover)  MODE=recover ;;
    --full)     FULL=1 ;;
    --only=*)   ONLY="${arg#--only=}" ;;
    *) echo "usage: $0 [--check|--recover] [--full] [--only=NN]" >&2; exit 2 ;;
  esac
done

targets() {
  python3 -c "
import glob, runpy
seen = []
for f in sorted(glob.glob('scripts/breaks/*.py')):
    t = runpy.run_path(f)['TARGET']
    if t not in seen:
        seen.append(t)
print('\n'.join(seen))"
}

# --- restore every target from the manifest, and PROVE it ------------------
restore_all() {
  local bad=0 f base want got
  [[ -f "$MANIFEST" ]] || return 0
  while read -r want f; do
    base=$(basename "$f")
    [[ -f "$ORIG/$base" ]] || { echo "  MISSING ORIGINAL for $f" >&2; bad=1; continue; }
    cp "$ORIG/$base" "$f"
    got=$(md5sum "$f" | cut -d' ' -f1)
    if [[ "$got" != "$want" ]]; then
      echo "  RESTORE VERIFY FAILED: $f ($got != $want)" >&2
      bad=1
    fi
  done < "$MANIFEST"
  rm -f "$ACTIVE"
  return $bad
}

# --- is any target currently different from its original? ------------------
dirty_report() {
  local f base want got dirty=0
  [[ -f "$MANIFEST" ]] || { echo "  no state directory - nothing was left applied"; return 0; }
  while read -r want f; do
    got=$(md5sum "$f" 2>/dev/null | cut -d' ' -f1)
    if [[ "$got" != "$want" ]]; then
      echo "  ⚠️  LIVE BREAK: $f differs from its original ($got != $want)"
      dirty=1
    fi
  done < "$MANIFEST"
  [[ $dirty -eq 0 ]] && echo "  all guard files match their originals"
  return $dirty
}

# --- recover from a previous run that was killed ---------------------------
# Runs BEFORE anything else, every time. A killed run leaves the state dir
# behind on purpose; finding one means api/ may still have the safety off.
preflight() {
  [[ -d "$STATE" ]] || return 0
  echo "=================================================================="
  echo "⚠️  STATE FROM AN EARLIER RUN FOUND - it did not finish cleanly."
  [[ -f "$ACTIVE" ]] && echo "   it died with this break applied: $(cat "$ACTIVE")"
  echo "=================================================================="
  dirty_report
  echo "  restoring from $ORIG ..."
  if restore_all; then
    echo "  RECOVERED ✓ - every guard file is back to its original"
    rm -rf "$STATE"
    return 0
  fi
  echo "  RECOVERY FAILED - refusing to run. Fix api/ by hand before retrying."
  return 1
}

case "$MODE" in
  check)
    echo "BREAK PASS - state check"
    dirty_report; exit $?
    ;;
  recover)
    preflight; exit $?
    ;;
esac

preflight || exit 1

# --- ONE AT A TIME -------------------------------------------------------
# Two concurrent runs mutate the same files and share one test database.
# That produced hours of results that looked like code regressions: breaks
# reappearing after being restored, deadlocked TRUNCATEs, and a suite whose
# failures moved between runs. An exclusive lock, not a courtesy check.
exec 9>"$STATE.lock"
if ! flock -n 9; then
  echo "ANOTHER BREAK PASS IS ALREADY RUNNING (lock: $STATE.lock) - refusing."
  echo "It mutates api/ and shares the test database; two at once corrupts both."
  exit 1
fi
if pgrep -f 'scripts/test\.sh' | grep -qv $$; then
  echo "A test run is already in flight - refusing, it would deadlock the test DB."
  echo "  $(pgrep -af 'scripts/test\.sh' | head -3)"
  exit 1
fi

mapfile -t TARGETS < <(targets)
mkdir -p "$ORIG"
: > "$MANIFEST"
for f in "${TARGETS[@]}"; do
  cp "$f" "$ORIG/$(basename "$f")"
  echo "$(md5sum "$f" | cut -d' ' -f1) $f" >> "$MANIFEST"
done
sync

# EXIT alone does not fire for SIGTERM/SIGINT, which is how `timeout` and the
# harness cap kill this. Trap them explicitly.
cleanup() {
  local rc=$?
  echo
  echo "  restoring guard files ..."
  if restore_all; then
    echo "  RESTORE VERIFIED ✓ (md5 matches the manifest for every file)"
    rm -rf "$STATE"
  else
    echo "  ⚠️  RESTORE FAILED - state kept at $STATE. Run: $0 --recover"
  fi
  exit $rc
}
trap cleanup EXIT
trap 'echo; echo "  interrupted - restoring"; exit 130' INT TERM HUP

fail=0
run=0

for spec in scripts/breaks/*.py; do
  base=$(basename "$spec")
  [[ -n "$ONLY" && "$base" != "$ONLY"* ]] && continue
  run=$((run + 1))

  # NUL-delimited: LABEL contains spaces and word-splitting mangled TARGET.
  { IFS= read -r -d '' LABEL; IFS= read -r -d '' TARGET; IFS= read -r -d '' EXPECT; } \
    < <(python3 -c "
import runpy, sys
m = runpy.run_path('$spec')
for k in ('LABEL', 'TARGET', 'EXPECT'):
    sys.stdout.write(m[k] + '\0')")

  echo
  echo "=================================================================="
  echo "BREAK [$base]: $LABEL"
  echo "  file: $TARGET"
  echo "=================================================================="

  before=$(md5sum "$TARGET" | cut -d' ' -f1)

  # ---- the named test must PASS with the guard in place --------------------
  # Symmetric to "a break that did not land is not a break": a test that was
  # already failing goes red whether or not the break landed, so it cannot be
  # evidence that the guard is covered. Cheap in targeted mode, and it is the
  # thing that makes targeted mode sound.
  if [[ $FULL -eq 0 ]]; then
    if ! ./scripts/test.sh -q -k "$EXPECT" > /tmp/bp_pre 2>&1; then
      if grep -qE "no tests ran|ERROR: not found" /tmp/bp_pre; then
        echo "  BASELINE: no test named '$EXPECT' exists <-- not coverage"
      else
        echo "  BASELINE: '$EXPECT' is ALREADY RED with the guard in place"
        echo "            -> it would go red either way. Not evidence."
        grep -E "^(FAILED|ERROR)" /tmp/bp_pre | head -3 | sed 's/^/      /'
      fi
      fail=1; continue
    fi
    echo "  BASELINE: $EXPECT passes with the guard in place ✓"
  fi

  echo "$base -> $TARGET" > "$ACTIVE"     # journal: what is applied right now

  if ! python3 -c "
import runpy, sys
m = runpy.run_path('$spec')
p = m['TARGET']; s = open(p).read()
n = s.count(m['OLD'])
if n != 1:
    sys.exit(f'anchor appears {n} times - break definition is stale')
open(p, 'w').write(s.replace(m['OLD'], m['NEW'], 1))
"; then
    echo "  EDIT APPLIED: NO (patch failed)"
    rm -f "$ACTIVE"; fail=1; continue
  fi
  after=$(md5sum "$TARGET" | cut -d' ' -f1)

  # ---- report whether the edit landed, BEFORE reading any test result ----
  if [[ "$before" == "$after" ]]; then
    echo "  EDIT APPLIED: NO  (file unchanged: $before)"
    echo "  -> A break that did not land is not a break. Not reading the tests."
    fail=1
  else
    echo "  EDIT APPLIED: YES ($before -> $after)"
    echo "  removed:"
    diff "$ORIG/$(basename "$TARGET")" "$TARGET" | grep '^<' | head -12 | sed 's/^/      /'

    if [[ $FULL -eq 1 ]]; then
      echo "  running FULL suite, expecting RED on $EXPECT ..."
      ./scripts/test.sh -q > /tmp/bp_out 2>&1; rc=$?
    else
      echo "  running $EXPECT, expecting RED ..."
      ./scripts/test.sh -q -k "$EXPECT" > /tmp/bp_out 2>&1; rc=$?
    fi

    if [[ $rc -eq 0 ]]; then
      echo "  RESULT: GREEN  <-- WRONG. The guard was removed and nothing failed."
      fail=1
    elif grep -qE "no tests ran|ERROR: not found" /tmp/bp_out; then
      echo "  RESULT: NO SUCH TEST '$EXPECT' <-- the break definition names a"
      echo "          test that does not exist. That is not coverage."
      fail=1
    elif grep -q "$EXPECT" /tmp/bp_out; then
      echo "  RESULT: RED ✓  ($(grep -oE '[0-9]+ failed' /tmp/bp_out | head -1)) and it was $EXPECT ✓"
    else
      echo "  RESULT: RED but '$EXPECT' did NOT fail - red for an unrelated reason"
      fail=1
    fi
  fi

  # ---- restore THIS file immediately, and verify, before the next break ----
  cp "$ORIG/$(basename "$TARGET")" "$TARGET"
  now=$(md5sum "$TARGET" | cut -d' ' -f1)
  if [[ "$now" == "$before" ]]; then
    echo "  restored ✓"
    rm -f "$ACTIVE"
  else
    echo "  ⚠️  RESTORE FAILED for $TARGET - stopping so nothing runs with a break live"
    fail=1
    break
  fi
done

echo
echo "=================================================================="
echo "FINAL VERIFY  ($run break$([[ $run -eq 1 ]] || echo s) exercised)"
echo "=================================================================="
restore_all && echo "  every guard file matches its original ✓" || fail=1
dirty_report || fail=1

echo "  running FULL suite, expecting GREEN ..."
if ./scripts/test.sh -q > /tmp/bp_out 2>&1; then
  echo "  RESULT: GREEN ✓ ($(grep -E '^=+ .*(passed|failed)' /tmp/bp_out | tail -1))"
else
  echo "  RESULT: RED <-- the code is not back to where it started"
  tail -12 /tmp/bp_out
  fail=1
fi

echo
if [[ $fail -eq 0 ]]; then
  echo "BREAK PASS: OK - every guard removal turned its own named test red,"
  echo "                 and the suite is green with all guards restored."
else
  echo "BREAK PASS: FAILED"
fi
exit $fail
