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
