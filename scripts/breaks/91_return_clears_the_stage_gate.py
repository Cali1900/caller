# THE CONFIRMED-EMAIL GATE. dialer.STAGE_DIALABLE refuses any lead that holds
# a confirmed email, and before migration 035 nothing ever cleared that flag on
# the way out of archive.
#
# So a lead archived for no_reply, bad_email or unsubscribed - the three
# reasons that can ONLY be reached from L2, because they all require email 1
# to have gone out - came back to the pool looking fresh and could never be
# dialed again. It could not be emailed either: emailed_at survived and
# mark_emailed() is write-once. Unreachable down both wires.
#
# Every test in tests/test_archive.py built its lead at stage='L1', which is
# what made the suppression test isolated AND what hid this for two days.
TARGET = 'api/archive.py'
EXPECT = 'test_a_lead_archived_from_L2_comes_back_dialable'
LABEL = 'let the return leave the lead at L2, undialable forever'
OLD = """                          has_confirmed_email = false,
                          email_confirmed_at = NULL,
"""
NEW = """"""
