# STAGE_DIALABLE is L1 only. At L2 we OWE them an email - dialing again asks
# a firm the question we are about to answer in writing.
#
# THIS BREAK USED TO WIDEN THE FILTER TO ('L1','L3') and it stopped guarding
# anything the day the stage constraint was narrowed: L3 is no longer a value
# the column accepts, so widening to include it excludes exactly what it
# excluded before and the named test stayed green. The full pass caught it.
#
# L2 is the only widening that is now possible, and it is the one that
# matters - a lead we have promised to write to, called anyway.
TARGET = 'api/dialer.py'
EXPECT = 'test_only_l1_is_a_dial_candidate'
LABEL = 'let the L1 campaign dial leads we owe an email'
OLD = """STAGE_DIALABLE = "AND l.stage = 'L1'\""""
NEW = """STAGE_DIALABLE = "AND l.stage IN ('L1', 'L2')\""""
