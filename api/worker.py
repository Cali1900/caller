"""
Caller worker: two loops in one process.

  drain   every 10s  - process the webhook inbox
  dialer  every 2m   - select, claim, guard, dial

Both are wrapped so an exception in one tick logs and the loop continues. A
worker that dies on a single bad webhook stops dialing entirely, and the
symptom looks like "Retell went quiet" rather than "we crashed".
"""

import signal
import sys
import time
import traceback

HEARTBEAT = '/tmp/caller_worker_heartbeat'
TICK_SECONDS = 5
DRAIN_EVERY = 10
DIAL_EVERY = 120

_stop = False


def _handle_stop(signum, _frame):
    global _stop
    _stop = True
    print(f'[worker] signal {signum}, draining', flush=True)


def _safe(label, fn, *args):
    try:
        return fn(*args)
    except Exception:
        print(f'[worker] {label} tick failed:\n{traceback.format_exc()}', flush=True)
        return None


def main():
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    from api import dialer, drain
    from api.config import load_config

    # Fail fast and loudly on bad config rather than idling in a loop that
    # looks healthy while being misconfigured.
    cfg = load_config()
    print(
        f'[worker] up. DIAL_MODE={cfg.DIAL_MODE!r} '
        f'allowlist={len(cfg.DIAL_ALLOWLIST)} entries.',
        flush=True,
    )
    if cfg.DIAL_MODE == 'allowlist' and not cfg.DIAL_ALLOWLIST:
        print('[worker] allowlist is EMPTY - nothing can be dialed. '
              'That is correct, not a bug.', flush=True)

    last_drain = 0.0
    last_dial = 0.0

    while not _stop:
        now = time.time()

        if now - last_drain >= DRAIN_EVERY:
            last_drain = now
            n = _safe('drain', drain.drain_once)
            if n:
                print(f'[worker] drained {n} event(s)', flush=True)

        if now - last_dial >= DIAL_EVERY:
            last_dial = now
            n = _safe('dialer', dialer.run_once, cfg)
            if n:
                print(f'[worker] placed {n} call(s)', flush=True)

        with open(HEARTBEAT, 'w') as f:
            f.write(str(time.time()))
        time.sleep(TICK_SECONDS)

    print('[worker] stopped', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
