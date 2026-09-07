#!/usr/bin/env bash
# BREAK PASS - prove the guard tests are not decoration.
#
# For each guard: remove it, confirm THE EDIT ACTUALLY LANDED, run the suite,
# require it to go RED, then restore and require GREEN again.
#
# "A break that did not land is not a break." The edit is verified by content
# hash and the removed lines are printed BEFORE any test result is read, so a
# silently-failed patch can never be misread as "the guard is covered".
#
# NOTE: tests run in the container with api/ MOUNTED (see scripts/test.sh).
# If api/ came from the baked image instead, every edit below would land on
# the host and change nothing in the test run - a green suite that proves
# nothing. That is the subtle version of the same failure.
set -uo pipefail
cd "$(dirname "$0")/.."

TARGETS=(api/guards.py api/dialer.py)
BACKUP_DIR=$(mktemp -d)
for f in "${TARGETS[@]}"; do cp "$f" "$BACKUP_DIR/$(basename "$f")"; done
restore_all() {
  for f in "${TARGETS[@]}"; do cp "$BACKUP_DIR/$(basename "$f")" "$f"; done
}
trap 'restore_all; rm -rf "$BACKUP_DIR"' EXIT

fail=0

run_break() {
  local label="$1" target="$2" py="$3" expect_test="$4"
  echo
  echo "=================================================================="
  echo "BREAK: $label"
  echo "  file:   $target"
  echo "=================================================================="

  restore_all
  local before after
  before=$(md5sum "$target" | cut -d' ' -f1)
  python3 -c "$py" || { echo "  EDIT APPLIED: NO (patch script errored)"; fail=1; return; }
  after=$(md5sum "$target" | cut -d' ' -f1)

  # ---- report whether the edit landed, BEFORE reading any test result ----
  if [[ "$before" == "$after" ]]; then
    echo "  EDIT APPLIED: NO  (file unchanged: $before)"
    echo "  -> A break that did not land is not a break. Not reading the tests."
    fail=1
    return
  fi
  echo "  EDIT APPLIED: YES ($before -> $after)"
  echo "  removed:"
  diff "$BACKUP_DIR/$(basename "$target")" "$target" | grep '^<' | sed 's/^/      /'

  echo "  running suite, expecting RED..."
  if ./scripts/test.sh -q >/tmp/bp_out 2>&1; then
    echo "  RESULT: GREEN  <-- WRONG. The guard was removed and nothing failed."
    echo "  -> This guard's tests are decoration."
    fail=1
  else
    echo "  RESULT: RED ✓  ($(grep -oE '[0-9]+ failed' /tmp/bp_out | head -1))"
    if [[ -n "$expect_test" ]]; then
      if grep -q "$expect_test" /tmp/bp_out; then
        echo "  and the EXPECTED test failed: $expect_test ✓"
      else
        echo "  but '$expect_test' did NOT fail - the suite went red for another reason"
        fail=1
      fi
    fi
  fi
}

# --- 1: allowlist membership check ----------------------------------------
run_break "delete the allowlist check" api/guards.py "
p='api/guards.py'; s=open(p).read()
old='''    if phone_e164 not in cfg.DIAL_ALLOWLIST:
        raise DialRefused(f'{phone_e164} not in dev allowlist')'''
assert old in s, 'anchor not found - break script is stale'
open(p,'w').write(s.replace(old,'    return'))
" "test_empty_allowlist_refuses_every_number"

# --- 2: unknown-mode check -------------------------------------------------
run_break "delete the unknown-DIAL_MODE check" api/guards.py "
p='api/guards.py'; s=open(p).read()
old='''    if mode != 'allowlist':
        raise DialRefused(f'unknown DIAL_MODE {mode!r} - refusing')'''
assert old in s, 'anchor not found - break script is stale'
open(p,'w').write(s.replace(old,'    pass'))
" "test_unknown_mode_refuses"

# --- 3: suppression JOINED INTO THE SELECTION QUERY ------------------------
run_break "remove suppression from the selection query" api/dialer.py "
p='api/dialer.py'; s=open(p).read()
old='''SUPPRESSION_JOIN = (
    'AND NOT EXISTS (SELECT 1 FROM suppression s '
    'WHERE s.phone_e164 = l.phone_e164)'
)'''
assert old in s, 'anchor not found - break script is stale'
open(p,'w').write(s.replace(old, \"SUPPRESSION_JOIN = ''\"))
" "test_suppressed_number_is_never_a_candidate"

# --- 4: suppression RE-CHECKED BEFORE THE DIAL -----------------------------
run_break "remove the pre-dial suppression re-check" api/guards.py "
p='api/guards.py'; s=open(p).read()
old='''    with conn.cursor() as cur:
        cur.execute(
            'SELECT reason FROM suppression WHERE phone_e164 = %s',
            (phone_e164,),
        )
        row = cur.fetchone()
    if row is not None:
        raise DialRefused(f'{phone_e164} is suppressed ({row[\"reason\"]})')'''
assert old in s, 'anchor not found - break script is stale'
open(p,'w').write(s.replace(old,'    return'))
" "test_suppressed_after_claim_is_refused_before_dialing"

# --- restore and require GREEN ---------------------------------------------
echo
echo "=================================================================="
echo "RESTORE"
echo "=================================================================="
restore_all
ok=1
for f in "${TARGETS[@]}"; do
  if ! cmp -s "$f" "$BACKUP_DIR/$(basename "$f")"; then
    echo "  RESTORE APPLIED: NO  ($f differs from original)"; ok=0; fail=1
  fi
done
[[ $ok -eq 1 ]] && echo "  RESTORE APPLIED: YES (all guard files match originals)"

echo "  running suite, expecting GREEN..."
if ./scripts/test.sh -q >/tmp/bp_out 2>&1; then
  echo "  RESULT: GREEN ✓ ($(tail -1 /tmp/bp_out))"
else
  echo "  RESULT: RED <-- restore did not work"; tail -8 /tmp/bp_out; fail=1
fi

echo
if [[ $fail -eq 0 ]]; then
  echo "BREAK PASS: OK - every guard removal turned the suite red."
else
  echo "BREAK PASS: FAILED"
fi
exit $fail
