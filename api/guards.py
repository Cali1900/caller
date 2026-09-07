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
