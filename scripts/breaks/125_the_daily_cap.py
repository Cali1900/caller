# THE DAILY CAP IS THE WARM-UP DIAL. 50 to start, and the database refuses above
# 250. Its day boundary is the OPERATOR's, matching guards.assert_under_daily_cap:
# a rolling 24 hours would mean "50 a day" never refilled at a predictable time.
TARGET = 'api/drip.py'
EXPECT = 'test_the_daily_cap_holds_the_rest'
LABEL = "ignore today's send count"
OLD = """DAILY_CAP = f\"\"\"
    AND ({_MAILBOX_SENDS}
           AND (es.sent_at AT TIME ZONE %(op_tz)s)::date
               = (now() AT TIME ZONE %(op_tz)s)::date) < c.email_daily_cap
\"\"\""""
NEW = """DAILY_CAP = ''"""
