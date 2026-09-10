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
# No daily maintenance loop any more. With a STANDING QUEUE there is nothing
# to enrol and nothing to roll over: a lead that is not reached simply stays
# queued and comes up again when it is next due.
# Scoring runs on EVERY call. As a queue drain, not inline in the webhook
# handler: the webhook drain must stay fast, and a transient LLM outage must
# not leave calls permanently unscored.
SCORE_EVERY = 60
# EMAIL. Both loops are gated OFF by default and stay off until a person throws
# a switch: email 1 needs the call campaign on email_1_mode='auto', and a drip
# step needs its drip campaign is_running. A tick with neither set does nothing.
#
# ⚠️ THE REPLY GATE IS NOT FAIL-CLOSED. Reply detection is MANUAL - Sean ticks a
# box and stages.record_reply() writes replied_at - so nothing here can know a
# firm has answered until he does. The window between a reply arriving and being
# ticked is real and cannot be closed without inbound ingest. That is accepted
# at this volume because he reads every reply, and the mitigation is that the
# drip never sends silently into the future: the digest lists tomorrow's sends
# by step and by firm, so there is a checkpoint BEFORE each batch. See
# api/drip.py and HANDOFF.md.
SEND_EVERY = 120

# ⚠️ THE EMAIL GAP'S FALLBACK, and it is the WIDE end on purpose - the same
# argument as FALLBACK_GAP for dials. Used only when no drip is running or the
# campaign row cannot be read.
FALLBACK_EMAIL_GAP = (60, 300)
# Alerts are immediate on purpose - a verbal yes decays. The digest is the
# batched channel; these two are not.
ALERT_EVERY = 60
# The digest goes out once, after the operator's day ends.
DIGEST_EVERY = 600
# The archive sweep. Hourly, not nightly: "nightly" needs a clock to be right
# about, and an hourly idempotent sweep returns a lead within an hour of its
# six-month mark without anyone reasoning about timezones or missed runs. It
# returns nothing until leads actually reach returns_at, so it is free.
ARCHIVE_EVERY = 3600
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


def _safe(label, fn, *args):
    try:
        return fn(*args)
    except Exception:
        print(f'[worker] {label} tick failed:\n{traceback.format_exc()}', flush=True)
        return None


# The spacing used when the running campaign cannot be read. Deliberately the
# wide default, not a tight one: unknown must never dial faster than configured.
FALLBACK_GAP = (210, 300)


def next_gap():
    """
    Seconds to wait before the next dial, re-rolled EVERY tick.

    Spacing belongs to the RUNNING campaign. With none running the loop still
    ticks (to drain, score and alert) but nothing dials, so the fallback only
    governs an idle loop.

    Module level rather than a closure in main() so the spacing test can call
    THIS function. It used to be nested, and the test reimplemented the roll
    against settings - which stopped being where spacing lives, so the test
    was asserting on a range production never read.
    """
    import random
    from api import campaigns as _c
    try:
        camp = _c.running()
    except Exception as exc:
        # FAILS SLOW, NEVER FAST. If the database cannot be read we do not know
        # the configured spacing, and the safe unknown is the wide default -
        # an outage must never be able to tighten the gap between calls. The
        # settings module used to own this property; it was deleted, so it
        # lives here now.
        print(f'[worker] cannot read the campaign ({exc}) - '
              f'falling back to {FALLBACK_GAP[0]}-{FALLBACK_GAP[1]}s', flush=True)
        camp = None
    lo = camp['dial_interval_min'] if camp else FALLBACK_GAP[0]
    hi = camp['dial_interval_max'] if camp else FALLBACK_GAP[1]
    if lo > hi:
        lo, hi = hi, lo
    return random.uniform(lo, hi)


def next_email_gap():
    """
    Seconds to wait before the next EMAIL, re-rolled every send.

    The same shape as next_gap() for dials, and for the same reason: a fixed
    cadence is itself a pattern, and 60 seconds apart to the millisecond looks
    more automated than a burst does.

    ⚠️ THE WIDEST RANGE ACROSS RUNNING DRIPS WINS - the SLOWEST. Spacing belongs
    to the mailbox, not to a campaign: two drips sharing info@counselorai.io
    cannot each claim their own gap without doubling the real rate. Taking the
    widest is the conservative reading, and it FAILS SLOW like the dial gap - if
    the database cannot be read we do not know the configured spacing, and an
    outage must never be able to tighten the interval between sends.
    """
    import random
    from api import db
    lo, hi = FALLBACK_EMAIL_GAP
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT max(email_gap_min_seconds) AS lo,
                                      max(email_gap_max_seconds) AS hi
                                 FROM campaign_configs
                                WHERE type = 'drip' AND is_running""")
                r = cur.fetchone()
        if r and r['lo'] is not None:
            lo, hi = r['lo'], r['hi']
    except Exception as exc:
        print(f'[worker] cannot read the email gap ({exc}) - '
              f'falling back to {lo}-{hi}s', flush=True)
    if lo > hi:
        lo, hi = hi, lo
    return random.uniform(lo, hi)


def main():
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    from api import (alerts, archive, dialer, drafts, drain, drip, scorer,
                     sender)
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
    from api import campaigns as _c
    _running = _c.running()
    if _running:
        print(f'[worker] campaign RUNNING: {_running["name"]} '
              f'(cap {_running["daily_cap"]}, L1 v{_running["agent_l1_version"]})',
              flush=True)
    else:
        print('[worker] NO CAMPAIGN RUNNING. Nothing will dial until one is '
              'started from /campaigns.', flush=True)

    import random

    _next_gap = next_gap

    last_drain = 0.0
    last_dial = 0.0
    dial_gap = _next_gap()
    last_score = 0.0
    last_email = 0.0
    email_gap = next_email_gap()
    last_alert = 0.0
    last_digest = 0.0
    last_archive = 0.0

    while not _stop:
        now = time.time()

        if now - last_drain >= DRAIN_EVERY:
            last_drain = now
            n = _safe('drain', drain.drain_once)
            if n:
                print(f'[worker] drained {n} event(s)', flush=True)

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

        # ⚠️ ONE EMAIL PER JITTERED GAP, NOT A BATCH PER TICK.
        #
        # This used to run every SEND_EVERY (120s) and drain up to 50 due leads
        # in a tight loop, with `limit=50` chosen for query cost and doing duty
        # as a rate. 100 leads entering a drip meant 50 emails in a few seconds,
        # then 50 more two minutes later: ~1,500/hour from one mailbox, which is
        # the burst pattern that costs a sending domain its reputation.
        #
        # The limit is now 1 and the PACE is the gap, so there is one mechanism
        # rather than two that have to agree. The hourly and daily caps live in
        # drip.due()'s selection - the gap alone would still permit 60/hour.
        #
        # ⚠️ ONE SLOT, SHARED, AND THE DRIP GOES FIRST. Both senders use the
        # same mailbox, so they cannot each have a slot. A drip step is a
        # promise already made to a firm that has heard from us; email 1 starts
        # a new conversation. The dialer makes the same call for the same
        # reason: a callback goes ahead of a new lead.
        if now - last_email >= email_gap:
            last_email = now
            d = _safe('drip', drip.run_once, cfg, 1)
            if d and (d['sent'] or d['refused']):
                print(f'[worker] drip: sent {d["sent"]}, '
                      f'refused {d["refused"]}', flush=True)
            if not (d and d['sent']):
                # EMAIL 1, for campaigns set to auto. Off by default.
                r = _safe('sender', sender.run_once, cfg, 1)
                if r and (r['sent'] or r['refused']):
                    print(f'[worker] email 1: sent {r["sent"]}, '
                          f'refused {r["refused"]}', flush=True)
            prev = email_gap
            email_gap = next_email_gap()
            if d and d['sent']:
                print(f'[worker] next email in ~{email_gap:.0f}s '
                      f'(waited {prev:.0f}s)', flush=True)

        if now - last_alert >= ALERT_EVERY:
            last_alert = now
            _safe('alerts.scan', alerts.scan, cfg)
            r = _safe('alerts.send', alerts.send_pending, cfg)
            if r and r['sent']:
                print(f'[worker] sent {r["sent"]} alert(s)', flush=True)

        if now - last_digest >= DIGEST_EVERY:
            last_digest = now
            _safe('digest', _maybe_digest, cfg)

        if now - last_archive >= ARCHIVE_EVERY:
            last_archive = now
            # Returns rested leads to the pool. Writes to `leads` ONLY -
            # suppression and the email do-not-send list are keyed on the
            # phone and the address, outlive the lead, and a lead coming
            # back out of archive is not evidence either should be forgotten.
            r = _safe('archive.return_due', archive.return_due)
            if r and r['returned']:
                print(f'[worker] returned {r["returned"]} lead(s) from archive '
                      f'to the pool ({", ".join(r["reasons"])})', flush=True)

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
