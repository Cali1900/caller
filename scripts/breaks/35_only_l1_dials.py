# INVERT THE FILTER. This break has now been rewritten TWICE for the same
# reason - each time, the "widening" it performed stopped being able to fail:
#
#   1. It widened to ('L1','L3'). Migration 027 narrowed the stage constraint
#      so L3 was no longer a value the column accepted, and widening to include
#      an impossible value excludes exactly what it excluded before. The named
#      test stayed green and the full pass caught it.
#   2. It widened to ('L1','L2'). Migration 035 made the column a BOOLEAN, so
#      "both values" is not a widening at all - it is identical to removing the
#      filter, which is already break 15. Two breaks doing one thing means one
#      guard nothing independently covers.
#
# An INVERSION is the realistic bug a boolean invites - a dropped NOT - and it
# is a genuinely different failure from removal: it dials ONLY the firms we owe
# an email to, and no others. Removal would still dial the right leads among
# the wrong ones; this dials exclusively the wrong ones.
TARGET = 'api/dialer.py'
EXPECT = 'test_only_l1_is_a_dial_candidate'
LABEL = 'drop the NOT, so ONLY the leads we owe an email dial'
OLD = "STAGE_DIALABLE = 'AND NOT l.has_confirmed_email'"
NEW = "STAGE_DIALABLE = 'AND l.has_confirmed_email'"
