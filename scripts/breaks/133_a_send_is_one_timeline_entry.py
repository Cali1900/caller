# A SEND WRITES TWICE and the timeline must render it ONCE.
#
# An activity row says "drip step 2 sent" as prose; the email_sends row knows the
# step position, the subject and the time. Lead detail renders the second, so it
# skips the first - without that, every email on the page appears twice, once in
# detail and once as a grey line, on the one screen whose job is to show the whole
# relationship in order.
#
# The skip is by NAMED KIND, not by matching summary text, except for the drip row
# whose kind is shared with STOP events - and a stop carries the reason, which is
# the whole point of recording it.
TARGET = 'api/web.py'
EXPECT = 'test_one_send_is_ONE_timeline_entry'
LABEL = 'render every email twice on lead detail'
OLD = """        if a['kind'] in _EMAIL_ACTIVITY_KINDS:
            continue          # rendered below, with the step and the subject
        if a['kind'] == 'drip' and _DRIP_SEND_SUMMARY.match(a['summary'] or ''):
            continue          # ditto - but a drip STOP is kept, it says why"""
NEW = """        pass"""
