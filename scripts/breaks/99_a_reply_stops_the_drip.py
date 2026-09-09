# A REPLY STOPS THE SEQUENCE DEAD.
#
# ⚠️ THIS GATE IS NOT FAIL-CLOSED, AND CANNOT BE. Reply detection is MANUAL -
# Sean ticks a box and stages.record_reply() writes replied_at - so nothing in
# code can know a firm has answered until he does. The window between a reply
# arriving and being ticked is real and uncloseable without inbound ingest.
#
# What this guards is the half that IS mechanical: once a reply is RECORDED,
# nothing more goes out. Losing it means following up on a firm that has already
# answered, which is the single most damaging thing this sequence can do.
TARGET = 'api/drip.py'
EXPECT = 'test_a_recorded_reply_stops_the_sequence'
LABEL = 'keep dripping a firm that has already replied'
OLD = """REPLIED_STOP = 'AND l.replied_at IS NULL'"""
NEW = """REPLIED_STOP = ''"""
