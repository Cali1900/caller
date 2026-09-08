TARGET = 'api/dialer.py'
EXPECT = 'test_a_tick_dials_only_max_concurrent_leads'
LABEL = 'ignore the campaign max_concurrent and dial the whole batch'
OLD = """    if limit is None:
        camp = campaigns.running()
        limit = camp['max_concurrent'] if camp else 1"""
NEW = """    if limit is None:
        limit = 50"""
