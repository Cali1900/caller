TARGET = 'api/guards.py'
EXPECT = 'test_the_cap_counts_new_leads'
LABEL = 'remove the daily new-lead cap'
OLD = """    if used >= cap:
        raise DialRefused(f'daily cap reached ({used}/{cap} new leads today)')"""
NEW = "    return"
