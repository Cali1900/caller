# to_email on a SENT draft records where the mail actually went. Rewriting it
# when the contact changes later would falsify history - the draft would claim
# it was sent to an address it never reached.
TARGET = 'api/drafts.py'
EXPECT = 'test_a_sent_draft_keeps_the_address_it_was_sent_to'
LABEL = 'let a corrected email rewrite an ALREADY SENT draft'
OLD = """            if row['emailed_at'] is not None:"""
NEW = """            if False:"""
