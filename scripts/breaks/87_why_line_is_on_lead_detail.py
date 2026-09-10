# ⚠️ REPLACED 2026-09-10. This break used to assert the opposite: that the "why is
# it here" line rendered on the LEADS LIST as well as lead detail, on the argument
# that a search result you have to click through has not answered your question.
#
# It was removed from the list on purpose - it wrapped every row onto three lines
# and made the table unscannable, which cost more than it explained. So the old
# break guarded a requirement that no longer exists, and its EXPECT test now
# asserts the reverse.
#
# The half that still matters is guarded here instead: THE EXPLANATION MUST BE
# SOMEWHERE. Dropping it from lead detail too would leave nowhere on the app that
# answers "why is this lead in this state", and nothing would fail.
TARGET = 'api/web.py'
EXPECT = 'test_the_why_line_is_on_lead_detail_and_NOT_on_the_list'
LABEL = 'stop rendering the why line on lead detail as well'
OLD = """        why_line = _why.line(_why_row(conn, lead_id))"""
NEW = """        why_line = ''"""
