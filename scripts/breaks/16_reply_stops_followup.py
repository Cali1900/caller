TARGET = 'api/dialer.py'
EXPECT = 'test_replied_at_alone_stops_the_dialer'
LABEL = 'keep calling someone who already replied to the email'
OLD = 'REPLIED_GUARD = "AND l.replied_at IS NULL"'
NEW = "REPLIED_GUARD = ''"
