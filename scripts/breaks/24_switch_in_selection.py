# The early return is the ONLY thing enforcing "a stopped campaign produces no
# candidates" - CAMPAIGN_MEMBERSHIP cannot help, because with nothing running
# there is no campaign_id to filter on.
#
# The previous break substituted campaign_id=NULL, which made the query match
# nothing all by itself, so removing the guard still returned [] and the break
# reported the guard as covered. A break that cannot fail is not a break.
#
# This one is the realistic bug shape instead: select for SOME campaign rather
# than THE RUNNING one.
TARGET = 'api/dialer.py'
EXPECT = 'test_pausing_stops_selection_immediately'
LABEL = 'select for any saved campaign instead of only the running one'
OLD = """    campaign = campaigns.running()
    if not campaign:
        return []"""
NEW = """    campaign = campaigns.running() or (campaigns.list_all() or [None])[0]
    if not campaign:
        return []"""
