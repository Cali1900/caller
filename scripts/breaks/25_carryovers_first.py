# A carry-over is a promise already made: a callback someone asked for, or a
# retry we owe. It goes ahead of fresh leads, and it does not consume the daily
# new-lead cap. Re-anchored after fresh selection became randomised.
TARGET = 'api/dialer.py'
EXPECT = 'test_carryovers_are_dialed_before_new_leads'
LABEL = 'stop putting carry-overs ahead of new leads'
OLD = """     ORDER BY (l.first_dialed_at IS NULL),        -- carry-overs first"""
NEW = """     ORDER BY (l.first_dialed_at IS NOT NULL),    -- carry-overs LAST"""
