# A DISABLED STEP IS SKIPPED, NOT SENT.
#
# Turning a step off is a different intent from deleting it: "try the sequence
# without step 3 and put it back". Without this the toggle is decoration - the
# screen says the step is off and the sender mails it anyway, which is the worst
# possible combination because the operator has been told it will not happen.
#
# Ordering is still validated ACROSS disabled steps, so re-enabling one cannot
# produce a backwards sequence - the re-enable is a single checkbox with no
# validation of its own.
TARGET = 'api/drip.py'
EXPECT = 'test_a_disabled_step_is_never_sent'
LABEL = 'send a step the operator turned off'
OLD = """ENABLED_STOP = 'AND s.enabled'"""
NEW = """ENABLED_STOP = ''"""
