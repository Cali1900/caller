# The bulk button acts on the CURRENT FILTER. Ignoring it adds every lead in
# the database on one click - the "set I didn't mean" this was built to
# prevent, and not undoable in one step.
#
# Re-anchored after the filter list grew.
TARGET = 'api/web.py'
EXPECT = 'test_it_respects_the_active_filter'
LABEL = 'let the bulk add ignore the active filter'
OLD = """        f['q'], f['status'], f['stage'], f['needs_you'], 1, 0,
        f['email_state'], f['campaign_id_filter'], DEFAULT_SORT, DEFAULT_DIR,"""
NEW = """        '', '', '', '', 1, 0,
        '', '', DEFAULT_SORT, DEFAULT_DIR,"""
