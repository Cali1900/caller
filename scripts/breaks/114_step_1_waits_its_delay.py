# STEP 1 WAITS ITS OWN DELAY, MEASURED FROM ENTERING THE DRIP.
#
# Step 1 IS the first send, so it cannot be N days from itself - it is timed in
# minutes from leads.drip_entered_at, which is why that column exists. Ignoring
# the delay means "after 15 minutes" sends instantly: an imported batch of 500
# firms all mailed the second it is uploaded, with the screen saying otherwise.
#
# The delay is a rate control as much as a courtesy one. An upload is the moment
# a mistake is most likely, and a few minutes is the window in which it can still
# be stopped.
TARGET = 'api/drip.py'
EXPECT = 'test_step_1_waits_for_its_delay_before_sending'
LABEL = 'send step 1 immediately, ignoring its configured delay'
OLD = """           "      AND coalesce(l.drip_entered_at, 'epoch'::timestamptz)"
           "          + (coalesce(s.delay_minutes, 0) || ' minutes')::interval"
           "          <= now()))")"""
NEW = """           "      ))")"""
