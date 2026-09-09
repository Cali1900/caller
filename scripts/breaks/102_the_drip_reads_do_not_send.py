# THE EMAIL DO-NOT-SEND LIST APPLIES TO EVERY STEP, NOT JUST EMAIL 1.
#
# Keyed on the ADDRESS, which is the only reason it survives a lead being
# archived, returned to the pool, re-uploaded or deduplicated. A hard bounce or
# an unsubscribe must stop step 3 as firmly as it stops step 1.
#
# The brief is explicit: the exclusions apply to EVERY step. A list that holds
# for the first email and not the rest is worse than none, because the first
# send is the one someone checked by hand.
TARGET = 'api/drip.py'
EXPECT = 'test_the_do_not_send_list_stops_every_step'
LABEL = 'let the drip mail an address on the do-not-send list'
OLD = """DO_NOT_SEND_STOP = ('AND NOT EXISTS (SELECT 1 FROM email_do_not_send d '
                    'WHERE d.email = lower(btrim(l.dm_email)))')"""
NEW = """DO_NOT_SEND_STOP = ''"""
