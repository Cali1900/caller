TARGET = 'api/guards.py'
EXPECT = 'test_a_carryover_is_exempt_from_the_cap'
LABEL = 'make carry-overs consume the daily new-lead cap'
OLD = """    if lead.get('first_dialed_at') is not None:
        return                      # already-started lead: not new, never capped"""
NEW = "    pass"
