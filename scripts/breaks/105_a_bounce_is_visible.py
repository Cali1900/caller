# A BOUNCE MUST BE VISIBLE, NOT JUST BLOCKED.
#
# Putting the address on the do-not-send list is what stops us mailing it again.
# It is NOT what tells anyone it happened. Without the status change the lead
# sits at 'emailed' with no drip and no explanation: it appears in no filter, on
# no queue, and simply stops.
#
# A LEAD FAILING SILENTLY is the shape every other guard in this system exists to
# prevent - the same family as the digest that dropped evening calls, deploy.sh
# reporting a pause it never performed, and the break pass reporting GREEN with
# no count. In each case the thing worked or failed invisibly and the report said
# nothing.
#
# NOT archived, deliberately: a bounce is a bad ADDRESS, not a bad firm, and it
# usually wants a corrected one - which is a person's job and needs the lead in
# front of them rather than resting six months.
# ⚠️ RETARGETED 2026-09-10, after this break went GREEN on the full pass. It
# removed a second `UPDATE leads SET status='bad_email'` in the bounce branch -
# which had become DEAD CODE when stop() started setting the status generically
# from STOP_STATUS, in the transaction that records the stop. The property was
# intact and the break was pointing at a line that no longer did the work.
#
# It now removes the mapping itself: a bounce that leaves the lead at `emailed`
# is a lead in no filter and on no queue, stopped with nothing saying why.
TARGET = 'api/drip.py'
EXPECT = 'test_a_bounce_is_visible_in_the_status_filter'
LABEL = 'block a bounced address without ever saying it bounced'
OLD = '''    'bounced': 'bad_email','''
NEW = '''    'bounced': 'emailed','''
