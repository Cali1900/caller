# REMOVE THE FILTER ENTIRELY. Once a confirmed email is captured we owe the
# firm a send and have not made it; dialing again asks the question we are
# about to answer in writing.
TARGET = 'api/dialer.py'
EXPECT = 'test_l2_is_never_a_dial_candidate'
LABEL = 'let a lead we owe an email dial (filter removed)'
OLD = "STAGE_DIALABLE = 'AND NOT l.has_confirmed_email'"
NEW = "STAGE_DIALABLE = ''"
