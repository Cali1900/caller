# Fails CLOSED. Anything that is not exactly 'unrestricted' or 'allowlist' -
# an empty string, whitespace, a typo, the wrong case - must refuse. A typo in
# EMAIL_MODE that silently meant "send" is the whole failure mode.
TARGET = 'api/guards.py'
EXPECT = 'test_an_unknown_mode_refuses'
LABEL = 'let an unknown EMAIL_MODE send anyway'
OLD = """    if mode != 'allowlist':
        raise EmailRefused(f'unknown EMAIL_MODE {mode!r} - refusing')"""
NEW = "    return"
