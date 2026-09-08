# There was NO path from human_review to a draft. generate_for() refuses an
# unconfirmed email, and its only caller was the "regenerate" button, which the
# page hides when no draft exists - so a lead the agent failed to get a
# confirmation for was stuck with no button to press.
#
# Re-anchored after the same block gained the L1 -> L2 advance.
TARGET = 'api/web.py'
EXPECT = 'test_confirming_by_hand_generates_the_draft'
LABEL = 'make a hand-confirmed email a dead end again (no draft)'
OLD = """        if drafts_mod.get(lead_id) is None:
            if drafts_mod.generate_for(lead_id):
                msg = msg.rstrip('.') + '. Draft generated.'"""
NEW = """        pass"""
