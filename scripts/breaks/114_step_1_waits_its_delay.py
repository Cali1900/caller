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
# ⚠️ RETARGETED 2026-09-10: step 1's clock is status_changed_at, not
# drip_entered_at - there is no entry event once membership is derived, and the
# moment the lead began to qualify is the moment its status changed.
TARGET = 'api/drip.py'
EXPECT = 'test_step_1_waits_for_its_delay_before_sending'
LABEL = 'send step 1 immediately, ignoring its configured delay'
OLD = """  OR (l.emailed_at IS NULL AND s.position = 1"
           "      AND l.status_changed_at"
           "          + (coalesce(s.delay_minutes, 0) || \' minutes\')::interval"
           "          <= now()))")"""
NEW = """  OR (l.emailed_at IS NULL AND s.position = 1))")"""
