# ⚠️ A CEILING RAISED IN ONE PLACE IS A 500 IN ANOTHER.
#
# drip_steps_delay_minutes_sane is CHECK (delay_minutes <= 10080) - seven days.
# The step 1 input offered 0-60 DAYS and the save refused anything over seven
# with "43200 minutes is over a week": a number the operator never typed, in a
# unit they never chose.
#
# The first fix raised this ceiling to 60 to match the input and left the CHECK
# alone, so 30 days stopped being a refusal and became a CheckViolation - the
# same fault (a control offering a value the save refuses) one layer down and
# less legible. The constraint is the authority; this constant is that number in
# the operator's unit, and the input's max is the same number again.
TARGET = 'api/drip.py'
EXPECT = 'test_step_1_cannot_be_given_a_delay_the_database_would_refuse'
LABEL = "raise step 1's ceiling past the database CHECK"
OLD = """MAX_STEP1_DAYS = 7"""
NEW = """MAX_STEP1_DAYS = 60"""
