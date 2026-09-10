# THE HOURLY CAP IS THE PACE. Without it the gap alone permits 60/hour at its
# tightest setting, and the target is 12-15 from a domain with no sending
# history. A burst is the one mistake here that cannot be undone: a dropped step
# is resent in a minute, a domain reputation is earned back over months.
TARGET = 'api/drip.py'
EXPECT = 'test_the_hourly_cap_holds_the_rest'
LABEL = 'stop counting sends against the hourly cap'
OLD = """HOURLY_CAP = f\"\"\"
    AND ({_MAILBOX_SENDS}
           AND es.sent_at > now() - interval '1 hour') < c.email_hourly_cap
\"\"\""""
NEW = """HOURLY_CAP = ''"""
