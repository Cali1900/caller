# ⚠️ A GUARD WHOSE TWO COUNTS MEASURE DIFFERENT THINGS IS NOT A GUARD.
#
# `kept` counts posted steps WITH COPY. `rows` counted EVERY posted row, blank
# clones included. So a blank row could stand in the place of a real step that
# had been dropped: the two totals matched, and the count guard - the one added
# specifically to turn silent step loss into a visible refusal - stayed quiet
# over exactly the case it exists for.
#
# It cost a second way too. The template promises "leave blank to skip", and an
# untouched clone instead refused the whole save with "step 3 has no subject",
# so adding a step you then thought better of blocked saving the two you meant.
#
# Skipping a row with no subject and no body is safe precisely because there is
# nothing to lose, and it makes the two numbers mean the same thing.
TARGET = 'api/web.py'
EXPECT = 'test_a_blank_row_is_skipped_not_refused'
LABEL = 'count a blank clone as a posted step again'
OLD = """        if not _content(i):
            continue"""
NEW = """        if False:
            continue"""
