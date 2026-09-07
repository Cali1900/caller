TARGET = 'api/guards.py'
EXPECT = 'test_unknown_mode_refuses'
LABEL = 'delete the unknown-DIAL_MODE check'
OLD = """    if mode != 'allowlist':
        raise DialRefused(f'unknown DIAL_MODE {mode!r} - refusing')"""
NEW = "    pass"
