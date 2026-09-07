TARGET = 'api/dialer.py'
EXPECT = 'test_a_tick_dials_only_max_concurrent_leads'
LABEL = 'ignore max_concurrent (one tick fires a whole batch)'
OLD = """    if limit is None:
        limit = settings_mod.get('max_concurrent')"""
NEW = """    if limit is None:
        limit = 10"""
