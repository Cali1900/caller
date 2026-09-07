#!/usr/bin/env bash
# BREAK PASS - prove the guard tests are not decoration.
#
# For each guard in scripts/breaks/: remove it, confirm THE EDIT ACTUALLY
# LANDED, run the suite, require it to go RED *on the named test*, then
# restore and require GREEN again.
#
# "A break that did not land is not a break." The edit is verified by content
# hash and the removed lines are printed BEFORE any test result is read, so a
# silently-failed patch can never be misread as "the guard is covered".
#
# Requiring the EXPECTED test to fail matters as much as requiring redness:
# a guard whose removal breaks some unrelated test is still untested.
#
# NOTE: tests run in the container with api/ MOUNTED (see scripts/test.sh).
# If api/ came from the baked image, every edit here would land on the host
# and change nothing in the test run - a green suite that proves nothing.
set -uo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR=$(mktemp -d)
mapfile -t TARGETS < <(python3 -c "
import glob, runpy
seen=[]
for f in sorted(glob.glob('scripts/breaks/*.py')):
    t=runpy.run_path(f)['TARGET']
    if t not in seen: seen.append(t)
print('\n'.join(seen))")
for f in "${TARGETS[@]}"; do cp "$f" "$BACKUP_DIR/$(basename "$f")"; done
restore_all() { for f in "${TARGETS[@]}"; do cp "$BACKUP_DIR/$(basename "$f")" "$f"; done; }
trap 'restore_all; rm -rf "$BACKUP_DIR"' EXIT

fail=0

for spec in scripts/breaks/*.py; do
  read -r LABEL TARGET EXPECT < <(python3 -c "
import runpy; m=runpy.run_path('$spec')
print(m['LABEL'].replace(' ',' '), m['TARGET'], m['EXPECT'])")
  LABEL=${LABEL//$' '/ }

  echo
  echo "=================================================================="
  echo "BREAK: $LABEL"
  echo "  file: $TARGET"
  echo "=================================================================="

  restore_all
  before=$(md5sum "$TARGET" | cut -d' ' -f1)
  if ! python3 -c "
import runpy, sys
m = runpy.run_path('$spec')
p = m['TARGET']; s = open(p).read()
if m['OLD'] not in s:
    sys.exit('anchor not found - break definition is stale')
open(p,'w').write(s.replace(m['OLD'], m['NEW']))
"; then
    echo "  EDIT APPLIED: NO (patch failed)"; fail=1; continue
  fi
  after=$(md5sum "$TARGET" | cut -d' ' -f1)

  # ---- report whether the edit landed, BEFORE reading any test result ----
  if [[ "$before" == "$after" ]]; then
    echo "  EDIT APPLIED: NO  (file unchanged: $before)"
    echo "  -> A break that did not land is not a break. Not reading the tests."
    fail=1; continue
  fi
  echo "  EDIT APPLIED: YES ($before -> $after)"
  echo "  removed:"
  diff "$BACKUP_DIR/$(basename "$TARGET")" "$TARGET" | grep '^<' | head -12 | sed 's/^/      /'

  echo "  running suite, expecting RED on $EXPECT ..."
  if ./scripts/test.sh -q >/tmp/bp_out 2>&1; then
    echo "  RESULT: GREEN  <-- WRONG. The guard was removed and nothing failed."
    fail=1
  else
    echo "  RESULT: RED ✓  ($(grep -oE '[0-9]+ failed' /tmp/bp_out | head -1))"
    if grep -q "$EXPECT" /tmp/bp_out; then
      echo "  and the EXPECTED test failed ✓"
    else
      echo "  but '$EXPECT' did NOT fail - red for an unrelated reason"
      fail=1
    fi
  fi
done

echo
echo "=================================================================="
echo "RESTORE"
echo "=================================================================="
restore_all
ok=1
for f in "${TARGETS[@]}"; do
  cmp -s "$f" "$BACKUP_DIR/$(basename "$f")" || { echo "  RESTORE APPLIED: NO ($f)"; ok=0; fail=1; }
done
[[ $ok -eq 1 ]] && echo "  RESTORE APPLIED: YES (all guard files match originals)"
echo "  running suite, expecting GREEN..."
if ./scripts/test.sh -q >/tmp/bp_out 2>&1; then
  echo "  RESULT: GREEN ✓ ($(tail -1 /tmp/bp_out))"
else
  echo "  RESULT: RED <-- restore did not work"; tail -8 /tmp/bp_out; fail=1
fi

echo
[[ $fail -eq 0 ]] && echo "BREAK PASS: OK - every guard removal turned the suite red on its own test." \
                  || echo "BREAK PASS: FAILED"
exit $fail
