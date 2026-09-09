#!/usr/bin/env bash
# THE WHOLE PASS, IN CHUNKS, RESUMABLE.
#
# WHY THIS EXISTS. A full pass outgrew the ten-minute command cap. Every kill
# left a state dir holding a snapshot from when that run STARTED, and the next
# run's recovery would restore it over work done since - which destroyed real
# work twice. Chunking removes the kill instead of hardening the recovery:
# ten breaks finish well inside the cap, and each chunk cleans up after itself,
# so no stale snapshot is ever left behind.
#
# THE CHECKPOINT is what makes that true of this wrapper too. Running all
# eight chunks in one invocation would just move the cap up a level and get
# killed here instead. Each completed chunk is recorded, so a kill costs at
# most the chunk in flight and a re-run continues from the next one.
#
#   ./scripts/break_pass_all.sh            # run, or resume where it stopped
#   ./scripts/break_pass_all.sh --restart  # ignore the checkpoint, start over
#   ./scripts/break_pass_all.sh --status   # what is done, what is left
#
# The FULL suite runs once, with the final chunk - not after every chunk.
set -uo pipefail
cd "$(dirname "$0")/.."

SIZE=10
MODE=run
for arg in "$@"; do
  case "$arg" in
    --restart)  MODE=restart ;;
    --status)   MODE=status ;;
    --size=*)   SIZE="${arg#--size=}" ;;
    *) echo "usage: $0 [--restart|--status] [--size=N]" >&2; exit 2 ;;
  esac
done
[[ "$SIZE" =~ ^[0-9]+$ && $SIZE -ge 1 ]] || { echo "--size must be >= 1" >&2; exit 2; }

PROGRESS=.break_pass_progress
TOTAL=$(ls scripts/breaks/*.py | wc -l)
# The checkpoint is only valid for the set of definitions it was made against.
# Adding or renaming a break shifts every index after it, so a resume against a
# changed set would skip guards while reporting a complete pass.
FINGERPRINT=$(ls scripts/breaks/*.py | md5sum | cut -d' ' -f1)

# A STALE ANCHOR COSTS TWENTY MINUTES TO FIND at chunk eight, and a second
# to find here. It also catches the worse case - an anchor matching twice,
# where the patch hits an occurrence the named test does not read.
if ! python3 scripts/breaks_anchor_check.py; then
  echo
  echo "Fix the definitions above before running. A break whose anchor has"
  echo "moved is not a guard that passed - it is a guard nothing checked."
  exit 1
fi

done_through=0
if [[ -f "$PROGRESS" ]]; then
  read -r p_fp p_size p_done _ < "$PROGRESS"
  if [[ "$p_fp" == "$FINGERPRINT" && "$p_size" == "$SIZE" ]]; then
    done_through=$p_done
  elif [[ "$MODE" != restart ]]; then
    echo "checkpoint is stale (breaks or --size changed since it was written)."
    echo "starting over - nothing is skipped."
    rm -f "$PROGRESS"
  fi
fi
[[ "$MODE" == restart ]] && { rm -f "$PROGRESS"; done_through=0; }

if [[ "$MODE" == status ]]; then
  if [[ $done_through -ge $TOTAL ]]; then
    echo "all $TOTAL breaks passed; no chunk outstanding"
  elif [[ $done_through -eq 0 ]]; then
    echo "nothing recorded - the next run starts at break 1 of $TOTAL"
  else
    echo "breaks 1-$done_through of $TOTAL passed; the next run starts at $((done_through + 1))"
  fi
  exit 0
fi

if [[ $done_through -ge $TOTAL ]]; then
  echo "every chunk is already recorded green. Use --restart to run them again."
  exit 0
fi
[[ $done_through -gt 0 ]] && echo "resuming: breaks 1-$done_through already passed."

for ((from = done_through + 1; from <= TOTAL; from += SIZE)); do
  upto=$((from + SIZE - 1)); [[ $upto -gt $TOTAL ]] && upto=$TOTAL
  echo
  echo "################ breaks $from-$upto of $TOTAL ################"
  if ! ./scripts/break_pass.sh --from="$from" --limit="$SIZE"; then
    echo
    echo "CHUNK $from-$upto FAILED - stopping here."
    echo "Breaks 1-$((from - 1)) are recorded green; fix the failure and re-run"
    echo "$0 to continue from $from without repeating them."
    exit 1
  fi
  # Only recorded AFTER the chunk restored its files and verified them.
  echo "$FINGERPRINT $SIZE $upto" > "$PROGRESS"
  sync
done

rm -f "$PROGRESS"
echo
echo "BREAK PASS COMPLETE - all $TOTAL breaks, and the full suite green."
