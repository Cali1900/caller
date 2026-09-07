"""
THE DIAL GUARD.

A bad next_attempt_at calculation does not throw an exception. It calls forty
law firms at 6am. This is the only failure in the system that reaches
strangers and cannot be undone, so the guard is structural, not conditional.

This module is deliberately PURE: no database, no network, no imports from the
rest of the app. It takes a phone number and a config object and either returns
or raises. That is what makes it testable, and what makes the break-pass in
tests/test_guards.py mean something.

The caller (the dialer, phase 1) is responsible for writing the dial_audit row
on refusal. A silent refusal is how you spend an hour asking "why did nothing
dial".
"""


class DialRefused(Exception):
    """Raised when a number must not be dialed. Never caught silently."""


def assert_dialable(phone_e164: str, cfg) -> None:
    """
    Fails CLOSED. An error, a missing config, or an unrecognised mode all
    result in no call being placed.

    'unrestricted' is prod, and only prod. Anything that is not exactly
    'unrestricted' or 'allowlist' - including an empty string, whitespace, or
    a typo - refuses.
    """
    mode = cfg.DIAL_MODE            # 'allowlist' | 'unrestricted'

    if mode == 'unrestricted':
        return                       # prod, and only prod

    if mode != 'allowlist':
        raise DialRefused(f'unknown DIAL_MODE {mode!r} - refusing')

    if phone_e164 not in cfg.DIAL_ALLOWLIST:
        raise DialRefused(f'{phone_e164} not in dev allowlist')


# ---------------------------------------------------------------------------
# SUPPRESSION
#
# This one needs the database, so it cannot be pure like assert_dialable.
# It is deliberately kept in the same module anyway: both are guards, and a
# reader looking for "what stops a call" should find everything in one file.
#
# Suppression is enforced in TWO places, on purpose:
#
#   1. JOINED INTO THE SELECTION QUERY (see dialer.SUPPRESSION_JOIN) so a
#      suppressed number is never even a candidate. This is the primary
#      defence - a number on the list must not appear in a result set.
#
#   2. RE-CHECKED HERE immediately before the dial. The gap between selecting
#      a batch and dialing it is real: a call can end with "remove me" and
#      write a suppression row while an earlier batch is still in flight.
#      Checking once at selection time would dial that number anyway.
#
# Belt and braces is correct here. This table is the highest-liability object
# in the system - $500-$1,500 per violation, no cap.
# ---------------------------------------------------------------------------


def assert_not_suppressed(conn, phone_e164: str) -> None:
    """
    Raises DialRefused if the number is on the suppression list.

    Takes an open connection so it runs inside the SAME transaction as the
    claim and the dial. Opening its own connection would let a suppression
    row committed mid-transaction be missed.
    """
    with conn.cursor() as cur:
        cur.execute(
            'SELECT reason FROM suppression WHERE phone_e164 = %s',
            (phone_e164,),
        )
        row = cur.fetchone()
    if row is not None:
        raise DialRefused(f'{phone_e164} is suppressed ({row["reason"]})')


# ---------------------------------------------------------------------------
# THE DAILY CAP
#
# The cap is a TOTAL, not a fresh budget: 200 means 200 dials, never 200 fresh
# plus however many carry-overs happened to exist.
#
# The single exception is deliberate. A carry-over is a promise already made -
# a receptionist asked to be called back. A fresh lead is a cold call. If
# carry-overs alone meet or exceed the cap they still dial, and zero fresh are
# added. The cap bends for the promise and never for the cold list.
#
# Enrolment already limits how many fresh leads enter a campaign. This is the
# second, authoritative check at dial time: enrolment can be re-run, a cap can
# be lowered mid-day, and neither should be able to overshoot.
# ---------------------------------------------------------------------------


def assert_under_cap(conn, campaign_date, source: str) -> None:
    """Raises DialRefused when a FRESH lead would exceed the day's cap."""
    if source != 'fresh':
        return                      # promised callbacks beat cold calls

    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.daily_cap,
                      (SELECT count(*) FROM campaign_leads cl
                        WHERE cl.campaign_date = c.campaign_date
                          AND cl.dialed_at IS NOT NULL) AS dialed
                 FROM campaigns c
                WHERE c.campaign_date = %s""",
            (campaign_date,),
        )
        row = cur.fetchone()
    if row is None:
        raise DialRefused(f'no campaign for {campaign_date} - refusing')
    if row['dialed'] >= row['daily_cap']:
        raise DialRefused(
            f'daily cap reached ({row["dialed"]}/{row["daily_cap"]}) - refusing fresh'
        )


def assert_campaign_running(conn, campaign_date) -> None:
    """
    Re-checked immediately before the dial so PAUSE takes effect on leads that
    were already claimed. Without this, pressing pause still lets the current
    batch dial out, which is exactly the moment someone presses it.
    """
    with conn.cursor() as cur:
        cur.execute(
            'SELECT started_at, paused FROM campaigns WHERE campaign_date = %s',
            (campaign_date,),
        )
        row = cur.fetchone()
    if row is None:
        raise DialRefused(f'no campaign for {campaign_date} - refusing')
    if row['started_at'] is None:
        raise DialRefused(f'campaign {campaign_date} not started - refusing')
    if row['paused']:
        raise DialRefused(f'campaign {campaign_date} is paused - refusing')
