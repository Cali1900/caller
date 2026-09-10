# ⚠️ A COLUMN THAT REPORTS THE COMPUTATION IS NOT AN ANSWER.
#
# The roster showed the raw delay arithmetic: "step 1 - sep 15 1:38am". True, and
# outside every sending window, so nothing was ever going at 1:38am. The reader is
# left to do the last step themselves, and the whole point of business hours in
# the FIRM's timezone is that the last step is not obvious.
#
# next_open() advances the schedule to the first instant inside an enabled window
# in the lead's own timezone; the pace queue then moves it again. Same reasoning
# as showing the call queue's real spacing rather than the configured interval.
TARGET = 'api/drip.py'
EXPECT = 'test_a_send_time_is_moved_into_the_FIRMS_business_hours'
LABEL = 'show the raw schedule instead of when the mail will go'
OLD = """            at = next_open(max(r['next_due'], now),
                           r['timezone'] or op_tz, wins)"""
NEW = """            at = max(r['next_due'], now)"""
