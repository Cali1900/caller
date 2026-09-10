# ⚠️ A CLICK IS INTEREST, NOT AN ANSWER.
#
# clicks.record() advanced the lead to `engaged`, which conflated two different
# facts: they replied, or they read the sample. That was harmless while status
# governed dialling and a status change was cheap.
#
# Once membership in every drip became DERIVED FROM STATUS it stopped being
# harmless: a drip accepting `emailed` lost the lead the moment it clicked, so A
# FIRM THAT READ THE SAMPLE STOPPED HEARING FROM US - the worst version of the
# feature, and invisible, because nothing failed.
#
# `clicked` is its own rung between `emailed` and `engaged`, so the ladder stays
# forward-only and a reply still outranks a click. replied_at is untouched and
# separate: REPLIED_STOP is not a status check.
TARGET = 'api/clicks.py'
EXPECT = 'test_a_click_sets_clicked_not_engaged'
LABEL = 'record a click as an answer again'
OLD = """            pipeline.advance(cur, lead['lead_id'], 'clicked',
                             'clicked the sample link')"""
NEW = """            pipeline.advance(cur, lead['lead_id'], 'engaged',
                             'clicked the sample link')"""
