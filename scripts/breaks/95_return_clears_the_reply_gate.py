# THE SECOND GATE THE RETURN HAS TO CLEAR.
#
# dialer.REPLIED_GUARD is "AND l.replied_at IS NULL". Leave replied_at on the
# row and a firm that once said "not interested", was archived as refused and
# rested six months comes back to the pool and can never be dialed - identical
# shape to the stage gate (break 91), in the same function, hidden the same
# way: no test set replied_at on a lead that was archived and returned.
#
# Six months on it is a legitimate prospect again, which is the entire premise
# of archive_reason='refused' having a return date at all.
#
# This clears a BUSINESS gate and no compliance one: suppression is keyed on
# the phone and email_do_not_send on the address, neither on the lead. See
# test_a_suppressed_lead_that_replied_is_still_not_dialable.
TARGET = 'api/archive.py'
EXPECT = 'test_a_lead_that_replied_comes_back_dialable'
LABEL = 'let a six-month-old reply keep the lead undialable forever'
OLD = """                          replied_at = NULL,
                          reply_note = NULL,
                          replied_by = NULL,
"""
NEW = """"""
