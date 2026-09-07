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
# DIAL SPACING IS OPERATOR-CONTROLLED, NOT HARDCODED.
# The interval is re-rolled from settings on EVERY tick, random within
# [dial_interval_min, dial_interval_max]. A fixed cadence is itself a
# pattern; jitter is the point, not a nicety.
#
# The wait is purely TIME based and is re-armed at the START of a tick, so
# how the previous call ended - busy, no answer, anything - can never pull
# the next dial forward. Three busies in a row cannot fire three calls.
# Carry-overs AUTO-ENROL. Nobody approves a callback: a receptionist who said
# "try Tuesday" already gave the answer, and making a human re-approve it is
# how Tuesday gets missed. Rollover is idempotent, so running it on a tick is
# safe.
MAINTAIN_EVERY = 300
# Scoring runs on EVERY call. As a queue drain, not inline in the webhook
# handler: the webhook drain must stay fast, and a transient LLM outage must
# not leave calls permanently unscored.
SCORE_EVERY = 60
# Alerts are immediate on purpose - a verbal yes decays. The digest is the
# batched channel; these two are not.
ALERT_EVERY = 60
# The digest goes out once, after the operator's day ends.
DIGEST_EVERY = 600
DIGEST_AFTER_HOUR = 18

_stop = False


def _handle_stop(signum, _frame):
    global _stop
    _stop = True
    print(f'[worker] signal {signum}, draining', flush=True)


def _maybe_digest(cfg):
    """One email, after the operator's day ends. digests has a PK on the date,
    so a re-run cannot double-send."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from api import digest
    local = datetime.now(ZoneInfo(cfg.OPERATOR_TIMEZONE))
    if local.hour < DIGEST_AFTER_HOUR:
        return
    r = digest.send(cfg)
    if r.get('sent'):
        print(f'[worker] digest sent for {r["date"]}', flush=True)
    elif r.get('detail'):
        print(f'[worker] digest FAILED: {r["detail"]}', flush=True)


def _maintain(cfg):
    """Keep today's campaign correct: it exists, yesterday's undialed leads
    have carried over, and any new callbacks or retries are enrolled."""
    from api import campaigns
    campaigns.ensure(cfg)
    roll = campaigns.rollover(cfg)
    counts = campaigns.enroll(cfg)
    if roll['rolled_over'] or counts['callback'] or counts['retry'] or counts['fresh']:
        print(f'[worker] maintain: rollover={roll} enrol={counts}', flush=True)
    if roll['flagged']:
        print(f'[worker] {roll["flagged"]} lead(s) hit the '
              f'{campaigns.ROLLOVER_FLAG_DAYS}-day rollover flag', flush=True)


def _safe(label, fn, *args):
    try:
        return fn(*args)
    except Exception:
        print(f'[worker] {label} tick failed:\n{traceback.format_exc()}', flush=True)
        return None


def main():
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    from api import alerts, campaigns, dialer, drafts, drain, scorer
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

    import random
    from api import settings as settings_mod

    def _next_gap():
        s_ = settings_mod.all_settings()
        lo, hi = s_['dial_interval_min'], s_['dial_interval_max']
        if lo > hi:
            lo, hi = hi, lo
        return random.uniform(lo, hi)

    last_drain = 0.0
    last_dial = 0.0
    dial_gap = _next_gap()
    last_maintain = 0.0
    last_score = 0.0
    last_alert = 0.0
    last_digest = 0.0

    while not _stop:
        now = time.time()

        if now - last_drain >= DRAIN_EVERY:
            last_drain = now
            n = _safe('drain', drain.drain_once)
            if n:
                print(f'[worker] drained {n} event(s)', flush=True)

        if now - last_maintain >= MAINTAIN_EVERY:
            last_maintain = now
            _safe('maintain', _maintain, cfg)

        if now - last_score >= SCORE_EVERY:
            last_score = now
            r = _safe('scorer', scorer.score_pending, cfg)
            if r and (r['scored'] or r['failed']):
                print(f'[worker] scored {r["scored"]}, failed {r["failed"]}', flush=True)
            # Drafts are GENERATED here and never sent. Same tick as scoring
            # because both are post-call work that must not touch the dialer.
            d = _safe('drafts', drafts.generate_pending)
            if d and d['drafted']:
                print(f'[worker] drafted {d["drafted"]} email(s) - NOT sent', flush=True)

        if now - last_alert >= ALERT_EVERY:
            last_alert = now
            _safe('alerts.scan', alerts.scan, cfg)
            r = _safe('alerts.send', alerts.send_pending, cfg)
            if r and r['sent']:
                print(f'[worker] sent {r["sent"]} alert(s)', flush=True)

        if now - last_digest >= DIGEST_EVERY:
            last_digest = now
            _safe('digest', _maybe_digest, cfg)

        if now - last_dial >= dial_gap:
            # Re-arm BEFORE dialing, and re-roll the jitter, so the next dial
            # is a fixed wall-clock wait from this one no matter what the call
            # does or how long it takes.
            last_dial = now
            n = _safe('dialer', dialer.run_once, cfg)
            prev_gap = dial_gap
            dial_gap = _next_gap()
            if n:
                print(f'[worker] placed {n} call(s) after {prev_gap:.0f}s; '
                      f'next in ~{dial_gap:.0f}s', flush=True)

        with open(HEARTBEAT, 'w') as f:
            f.write(str(time.time()))
        time.sleep(TICK_SECONDS)

    print('[worker] stopped', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
