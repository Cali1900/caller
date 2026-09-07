#!/usr/bin/env bash
# BREAK PASS - prove the guard tests are not decoration.
#
# For each guard: remove it, confirm THE EDIT ACTUALLY LANDED, run the tests,
# require them to go RED, then restore and require GREEN again.
#
# "A break that did not land is not a break." The edit is verified and
# reported BEFORE the test result is read, so a silently-failed patch can
# never be misread as "the guard is covered".
set -uo pipefail
cd "$(dirname "$0")/.."

GUARD=api/guards.py
BACKUP=$(mktemp)
cp "$GUARD" "$BACKUP"
restore() { cp "$BACKUP" "$GUARD"; rm -f "$BACKUP"; }
trap restore EXIT

fail=0

run_break() {
  local label="$1" py="$2"
  echo
  echo "=================================================================="
  echo "BREAK: $label"
  echo "=================================================================="

  cp "$BACKUP" "$GUARD"
  local before after
  before=$(md5sum "$GUARD" | cut -d' ' -f1)
  python3 -c "$py"
  after=$(md5sum "$GUARD" | cut -d' ' -f1)

  # ---- report whether the edit landed, BEFORE reading any test result ----
  if [[ "$before" == "$after" ]]; then
    echo "  EDIT APPLIED: NO  (file unchanged: $before)"
    echo "  -> A break that did not land is not a break. Not reading the tests."
    fail=1
    return
  fi
  echo "  EDIT APPLIED: YES ($before -> $after)"
  echo "  removed lines:"
  diff "$BACKUP" "$GUARD" | grep '^<' | sed 's/^/      /'

  echo "  running tests, expecting RED..."
  if python3 -m pytest tests/ -q >/tmp/bp_out 2>&1; then
    echo "  RESULT: GREEN  <-- WRONG. The guard was removed and nothing failed."
    echo "  -> These tests are decoration."
    fail=1
  else
    echo "  RESULT: RED ✓  ($(grep -oE '[0-9]+ failed' /tmp/bp_out | head -1))"
  fi
}

# --- break 1: delete the allowlist membership check ------------------------
run_break "delete the allowlist check" "
import re
p='$GUARD'; s=open(p).read()
old='''    if phone_e164 not in cfg.DIAL_ALLOWLIST:
        raise DialRefused(f'{phone_e164} not in dev allowlist')'''
assert old in s, 'anchor not found - break script is stale'
open(p,'w').write(s.replace(old,'    return'))
"

# --- break 2: delete the unknown-mode check --------------------------------
run_break "delete the unknown-DIAL_MODE check" "
p='$GUARD'; s=open(p).read()
old='''    if mode != 'allowlist':
        raise DialRefused(f'unknown DIAL_MODE {mode!r} - refusing')'''
assert old in s, 'anchor not found - break script is stale'
open(p,'w').write(s.replace(old,'    pass'))
"

# --- restore and require GREEN ---------------------------------------------
echo
echo "=================================================================="
echo "RESTORE"
echo "=================================================================="
cp "$BACKUP" "$GUARD"
if md5sum -c <(echo "$(md5sum "$BACKUP" | cut -d' ' -f1)  $GUARD") >/dev/null 2>&1; then
  echo "  RESTORE APPLIED: YES (guard file matches original)"
else
  echo "  RESTORE APPLIED: NO  <-- guard file does NOT match original"
  fail=1
fi
echo "  running tests, expecting GREEN..."
if python3 -m pytest tests/ -q >/tmp/bp_out 2>&1; then
  echo "  RESULT: GREEN ✓ ($(tail -1 /tmp/bp_out))"
else
  echo "  RESULT: RED <-- restore did not work"
  tail -5 /tmp/bp_out
  fail=1
fi

echo
if [[ $fail -eq 0 ]]; then
  echo "BREAK PASS: OK - every guard removal turned the suite red."
else
  echo "BREAK PASS: FAILED"
fi
exit $fail
