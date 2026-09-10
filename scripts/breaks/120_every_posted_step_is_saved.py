# ⚠️ SILENT DATA LOSS: A SAVE THAT WROTE FEWER STEPS THAN WERE ON THE SCREEN.
#
# The handler walked i = 0, 1, 2 ... and STOPPED AT THE FIRST GAP. Any step whose
# index was not contiguous was dropped, and the save reported success - copy
# somebody had typed, destroyed, with a green banner.
#
# Nothing guarantees the DOM's indices stay contiguous. The list is a
# server-rendered set plus javascript clones; a removed step, a failed clone, a
# re-fired handler, any of it leaves a hole. A LOOP THAT STOPS AT A HOLE TREATS
# "I COULD NOT SEE IT" AS "IT IS NOT THERE" - and that is the whole family of
# faults this repo keeps paying for.
#
# Reading every posted index is the fix. The count guard beside it is the second
# line: whatever goes wrong between the form and the database, writing fewer
# steps than were posted must never look like success.
TARGET = 'api/web.py'
EXPECT = 'test_non_contiguous_indices_are_all_saved'
LABEL = 'stop reading steps at the first gap in the indices'
OLD = """    posted = sorted(seen)"""
NEW = """    posted = []
    _i = 0
    while _i in seen:
        posted.append(_i); _i += 1"""
