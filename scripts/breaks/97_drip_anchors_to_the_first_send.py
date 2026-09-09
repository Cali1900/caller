# THE SCHEDULE ANCHORS TO emailed_at - THE FIRST SEND - NEVER TO THE PREVIOUS
# STEP.
#
# Chaining lets the schedule drift by however long each send was late, and the
# drift compounds: a sequence meant to run 0/4/10/21 days becomes whatever the
# worker's timing happened to be. Anchoring cannot drift at all.
#
# This is also WHY emailed_at is write-once and mark_emailed() is a no-op on a
# second call - a restamp would move EVERY scheduled send at once. Break 18
# guards that half; this guards the arithmetic.
TARGET = 'api/drip.py'
EXPECT = 'test_delays_anchor_to_the_first_send_not_the_previous_step'
LABEL = 'chain each delay off the previous send instead of emailed_at'
OLD = """DUE_NOW = ("AND ((l.emailed_at IS NOT NULL"
           "      AND l.emailed_at + (s.delay_days || ' days')::interval"
           "          <= now())"
           "  OR (l.emailed_at IS NULL AND s.position = 1))")"""
NEW = """DUE_NOW = ("AND ((l.emailed_at IS NOT NULL"
           "      AND coalesce((SELECT max(es2.sent_at) FROM email_sends es2"
           "                     WHERE es2.lead_id = l.lead_id), l.emailed_at)"
           "          + (s.delay_days || ' days')::interval <= now())"
           "  OR (l.emailed_at IS NULL AND s.position = 1))")"""
