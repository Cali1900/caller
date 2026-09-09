# A CLICK IS INTEREST, NOT AN ANSWER, AND IT MUST NOT STOP THE SEQUENCE.
#
# The inverse break of 99, and the reason both exist: stopping on a click would
# silence the drip exactly when it is working. Someone who opened the sample and
# read it is the best prospect in the list and has said nothing yet.
#
# This break adds the stop that must NOT be there - it is red when a click
# silences the sequence, which is the bug a well-meaning "they engaged, back
# off" change would introduce.
TARGET = 'api/drip.py'
EXPECT = 'test_a_click_does_NOT_stop_the_sequence'
LABEL = 'let a click stop the drip'
OLD = """REPLIED_STOP = 'AND l.replied_at IS NULL'"""
NEW = """REPLIED_STOP = ('AND l.replied_at IS NULL AND NOT EXISTS ('
                'SELECT 1 FROM email_clicks ec WHERE ec.lead_id = l.lead_id)')"""
