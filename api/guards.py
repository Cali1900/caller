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


def assert_has_phone(lead) -> None:
    """
    Raises DialRefused when a lead has no number to dial.

    ⚠️ THIS REPLACES A SCHEMA GUARANTEE. leads.phone_e164 was NOT NULL until
    migration 038, so a lead without a number could not exist and therefore
    could not be dialled. Email-only leads made it nullable, and from that
    point only code stands between a phoneless lead and a call attempt.

    IT REFUSES LOUDLY, and that is the point of it existing separately from the
    selection filter. dialer.PHONE_REQUIRED already excludes these leads from
    being CANDIDATES - silently, which is correct for a filter. But a lead that
    reaches the dial path without a number is a DIFFERENT event: something put
    it there, and 'nothing happened' is the worst possible report. This one
    audits and prints, via the dialer's existing DialRefused handling.

    Without it, in unrestricted mode a NULL sails through assert_dialable
    (None is not in the allowlist, so allowlist mode refuses - but unrestricted
    returns early) and reaches retell.create_phone_call(to_number=None), which
    surfaces as a Retell API error and reads as a Retell problem rather than a
    bad lead.
    """
    phone = (lead or {}).get('phone_e164')
    if not (phone or '').strip():
        raise DialRefused(
            'no phone number on this lead - refusing. An email-only lead is '
            'not dialable; add a number AND a timezone to make it one.')


class EmailRefused(RuntimeError):
    """Raised when an email must not be sent. Never caught silently."""


def assert_emailable(address: str, cfg) -> None:
    """
    THE EMAIL HALF OF THE DIAL GUARD. Same shape, same failure mode, same
    reasoning: dialing a stranger and emailing one are the same mistake
    through different wires.

    Fails CLOSED. An error, a missing config, or an unrecognised mode all
    result in no email being sent.

    'unrestricted' is prod, and only prod. Anything that is not exactly
    'unrestricted' or 'allowlist' - including an empty string, whitespace, or
    a typo - refuses.

    An EMPTY allowlist sends NOTHING. That is the point of it: a dev box with
    no list configured must not be able to mail anyone at all.
    """
    mode = cfg.EMAIL_MODE

    if mode == 'unrestricted':
        return                       # prod, and only prod

    if mode != 'allowlist':
        raise EmailRefused(f'unknown EMAIL_MODE {mode!r} - refusing')

    if (address or '').strip().lower() not in cfg.EMAIL_ALLOWLIST:
        raise EmailRefused(f'{address} not in dev email allowlist')


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
# THE SWITCH
#
# This replaces the old START button. Removing the daily ritual removed what
# stopped "add 500 leads" becoming "dial 500 now" - and a guard by OMISSION
# disappears the moment the thing it depended on does. So it is now one
# explicit setting, DEFAULTING TO OFF, that a person turns on deliberately.
#
# Checked in the selection query AND here before the dial, because a batch can
# be claimed and then paused mid-flight.
# ---------------------------------------------------------------------------


def assert_campaign_running(campaign) -> None:
    """
    Raises DialRefused unless a campaign is actually running.

    A campaign is started deliberately and exactly one may run at a time -
    enforced by a unique partial index, not by this check. This is the
    per-dial half, for a lead claimed just before someone hit stop.
    """
    if not campaign or not campaign.get('is_running'):
        raise DialRefused('no campaign is running - refusing')


# ---------------------------------------------------------------------------
# THE DAILY CAP
#
# The cap counts NEW leads only, by leads.first_dialed_at. A callback, an L3
# follow-up or a retry is a promise already made and goes AHEAD of new leads
# without consuming the budget - the same asymmetry as before, expressed
# against a standing queue instead of a per-day campaign.
# ---------------------------------------------------------------------------


def assert_under_daily_cap(conn, lead, campaign, operator_tz: str) -> None:
    """
    Raises DialRefused when a NEW lead would exceed the CAMPAIGN's cap.

    The cap is per campaign and counts only leads belonging to it, so two
    campaigns cannot spend each other's budget.
    """
    if lead.get('first_dialed_at') is not None:
        return                      # already-started lead: not new, never capped

    cap = campaign['daily_cap']
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM leads
                WHERE campaign_id = %s
                  AND first_dialed_at IS NOT NULL
                  AND (first_dialed_at AT TIME ZONE %s)::date
                      = (now() AT TIME ZONE %s)::date""",
            (campaign['campaign_id'], operator_tz, operator_tz))
        used = cur.fetchone()['n']
    if used >= cap:
        raise DialRefused(f'daily cap reached ({used}/{cap} new leads today)')
