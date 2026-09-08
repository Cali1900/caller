# Precedence is the order things happen in: a reply outranks a click, a click
# outranks a send, a send outranks a draft. Drop the reply arm and a lead that
# WROTE BACK shows as merely clicked or sent - the one state that most needs a
# person disappears into the quiet pile.
TARGET = 'api/web.py'
EXPECT = 'test_a_reply_outranks_a_send'
LABEL = 'let a send outrank a reply in the follow-up column'
OLD = """    CASE WHEN l.replied_at IS NOT NULL           THEN 'replied'
         WHEN ck.clicks > 0                      THEN 'clicked'
         WHEN l.emailed_at IS NOT NULL           THEN 'sent'"""
NEW = """    CASE WHEN l.emailed_at IS NOT NULL           THEN 'sent'
         WHEN ck.clicks > 0                      THEN 'clicked'
         WHEN l.replied_at IS NOT NULL           THEN 'replied'"""
