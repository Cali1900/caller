# EMAIL 1 IS STEP 1, AND A CALL-SOURCED LEAD MUST NEVER RECEIVE STEP 1's COPY.
#
# A call-sourced lead's email 1 is recorded with step_id NULL, because no drip
# existed when it went. enter() links it to step 1 so ALREADY_SENT_STOP excludes
# that step - otherwise the firm gets the same opener twice, minutes apart.
#
# ⚠️ REWRITTEN 2026-09-09. The link alone was not enough: a lead that joined a
# drip BEFORE the sequence was written had no step 1 to link to, so when the
# steps were added the days branch matched step 1 (emailed_at set, delay_days 0)
# and a firm we CALLED received step 1's cold opener, which says nothing about
# the call. `s.position > 1` on the days branch is what actually closes it -
# step 1 cannot be reached down that path at all, whatever happened at entry.
#
# This break restores the days branch to matching step 1.
TARGET = 'api/drip.py'
EXPECT = 'test_a_call_sourced_lead_never_receives_step_1'
LABEL = 'let the days branch schedule step 1, so a called firm gets the cold opener'
OLD = '''DUE_NOW = ("AND ((l.emailed_at IS NOT NULL AND s.position > 1"'''
NEW = '''DUE_NOW = ("AND ((l.emailed_at IS NOT NULL"'''
