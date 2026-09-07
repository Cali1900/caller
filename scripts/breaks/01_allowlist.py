TARGET = 'api/guards.py'
EXPECT = 'test_empty_allowlist_refuses_every_number'
LABEL = 'delete the allowlist check'
OLD = """    if phone_e164 not in cfg.DIAL_ALLOWLIST:
        raise DialRefused(f'{phone_e164} not in dev allowlist')"""
NEW = "    return"
