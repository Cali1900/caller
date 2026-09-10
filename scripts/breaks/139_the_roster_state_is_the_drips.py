# ⚠️ leads.status IS THE CALL STATUS AND MEANS NOTHING HERE.
#
# The drip roster showed it, so a lead read "completed" - which says the dialer
# finished with it, not that its sequence is over. A lead can be 'completed' and
# mid-drip, and on this screen the only question ever asked is "where is this lead
# in the sequence".
#
# drip_state is derived from the same exclusions due() applies, so the roster and
# the sender cannot disagree about where a lead is.
TARGET = 'api/drip.py'
EXPECT = 'test_the_status_column_is_the_DRIPS_not_the_CALLS'
LABEL = 'let the drip roster fall back to the call status'
OLD = """        if r['replied_at']:
            r['drip_state'], r['state_detail'] = 'stopped', 'replied'"""
NEW = """        if True:
            r['drip_state'], r['state_detail'] = r['status'], ''"""
