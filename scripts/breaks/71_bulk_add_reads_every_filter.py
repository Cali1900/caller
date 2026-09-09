# The bulk button acts on the CURRENT FILTER - all of it. Reading a SUBSET is
# worse than ignoring the filter entirely: the confirm says "2 matching:
# state = NC" and the button then adds every lead in the database, because the
# state filter was never passed to the query that does the work.
#
# The anchor moved when the tag filter was added, and the pass refused to
# apply a stale definition rather than skipping it quietly - which is the
# only reason this was noticed at all. Kept in step with the call.
TARGET = 'api/web.py'
EXPECT = 'test_the_bulk_add_still_respects_the_filter'
LABEL = 'let the bulk add read only some of the active filters'
OLD = """        f['state'], f['city'], f['has_email'], f['agent_min'], f['agent_max'],
        f['outcome_min'], f['outcome_max'], f['vol_min'], f['vol_max'],
        f['called'], f['tag'])"""
NEW = """        '', '', '', '', '', '', '', '', '', '', '')"""
