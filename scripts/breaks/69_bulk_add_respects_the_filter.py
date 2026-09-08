# The bulk button acts on the CURRENT FILTER. Ignoring it adds every lead in
# the database to a campaign on one click - the exact "set I didn't mean" this
# was built to prevent, and it is not undoable in one step.
TARGET = 'api/web.py'
EXPECT = 'test_it_respects_the_active_filter'
LABEL = 'let the bulk add ignore the active filter'
OLD = """    _, _, where, cparams = _lead_query(
        f['q'], f['status'], f['stage'], f['needs_you'], 1, 0,
        f['email_state'], f['campaign_id_filter'])"""
NEW = """    _, _, where, cparams = _lead_query('', '', '', '', 1, 0, '', '')"""
