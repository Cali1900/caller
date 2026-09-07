"""
Caller worker.

Phase 0: a heartbeat only. It exists so the container, its healthcheck and
its restart behaviour are proven before anything in it can place a call.

Phase 1 adds the two loops: the dialer (every 2 minutes) and the webhook
drain (every 10 seconds). Neither is built yet - see CALLER_BUILD_PLAN
phase 1. Building them here would be building ahead.
"""

import os
import signal
import sys
import time

HEARTBEAT = '/tmp/caller_worker_heartbeat'
TICK_SECONDS = 10

_stop = False


def _handle_stop(signum, _frame):
    global _stop
    _stop = True
    print(f'[worker] signal {signum}, draining', flush=True)


def main():
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    # Fail fast and loudly if config is wrong, rather than idling in a loop
    # that looks healthy while being misconfigured.
    from api.config import load_config
    cfg = load_config()
    print(
        f'[worker] up. DIAL_MODE={cfg.DIAL_MODE!r} '
        f'allowlist={len(cfg.DIAL_ALLOWLIST)} entries. No dial loop in phase 0.',
        flush=True,
    )

    while not _stop:
        with open(HEARTBEAT, 'w') as f:
            f.write(str(time.time()))
        time.sleep(TICK_SECONDS)

    print('[worker] stopped', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
