TARGET = 'api/dialer.py'
EXPECT = 'test_a_lead_not_in_the_queue_is_never_a_candidate'
LABEL = 'dial leads that were never added to the queue'
OLD = "QUEUE_MEMBERSHIP = \"AND l.pool_status = 'active'\""
NEW = "QUEUE_MEMBERSHIP = ''"
