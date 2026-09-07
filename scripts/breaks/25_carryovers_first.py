TARGET = 'api/dialer.py'
EXPECT = 'test_carryovers_are_dialed_before_new_leads'
LABEL = 'stop putting carry-overs ahead of new leads'
OLD = "     ORDER BY (l.first_dialed_at IS NULL), l.next_attempt_at"
NEW = "     ORDER BY l.next_attempt_at"
