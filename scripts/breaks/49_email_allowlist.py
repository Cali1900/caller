# THE EMAIL HALF OF THE DIAL GUARD. Removing it means a dev box with no list
# configured can mail real law firms. Same mistake as dialing a stranger,
# through a different wire.
TARGET = 'api/guards.py'
EXPECT = 'test_an_empty_allowlist_sends_nothing'
LABEL = 'delete the email allowlist check'
OLD = """    if (address or '').strip().lower() not in cfg.EMAIL_ALLOWLIST:
        raise EmailRefused(f'{address} not in dev email allowlist')"""
NEW = "    return"
