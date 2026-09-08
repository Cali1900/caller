# "I emailed them" may only fire from L2, and only once. Dropping the stage and
# emailed_at conditions lets a double click - or a sender racing the button -
# RESTAMP emailed_at, which silently changes every "N minutes after send"
# already recorded against that lead. The first send is the one timings are
# measured from.
TARGET = 'api/stages.py'
EXPECT = 'test_clicking_twice_does_not_restamp_the_send_time'
LABEL = 'let "I emailed them" fire twice and restamp the send time'
OLD = """                    WHERE lead_id = %s AND stage = 'L2'
                      AND emailed_at IS NULL"""
NEW = """                    WHERE lead_id = %s"""
