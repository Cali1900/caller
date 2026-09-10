# REPUTATION BELONGS TO THE ADDRESS, NOT THE CAMPAIGN. Two drips sharing
# info@counselorai.io at 15/hour each would put 30/hour on one mailbox, and the
# mailbox is what a spam filter scores. Counting per campaign would make every
# cap a lie the moment a second drip existed.
# ⚠️ RETARGETED 2026-09-10: the mailbox is read from email_sends.from_email, not
# reached through the lead's campaign - that join went with drip_campaign_id, and
# was a guess anyway. Removing the predicate counts EVERY mailbox as one.
TARGET = 'api/drip.py'
# ⚠️ THE NEGATIVE TEST IS THE ONLY ONE THAT CAN DETECT THIS, and pointing at the
# positive one made the break report GREEN on the full pass.
#
# This widens the predicate to `WHERE true`, so the caps count EVERY mailbox as
# one. "A send from the same mailbox counts" still passes under that - it counts
# more, not less. The failure is over-counting, and only
# test_a_different_mailbox_does_not_spend_this_ones_budget asserts the exclusion
# that over-counting breaks.
#
# Same lesson as the stalled-sequence net: a break that REMOVES a filter can only
# be caught by a test asserting something is EXCLUDED.
EXPECT = 'test_a_different_mailbox_does_not_spend_this_ones_budget'
LABEL = 'count sends per campaign instead of per mailbox'
OLD = """         WHERE es.from_email = c.sender_email"""
NEW = """         WHERE true"""
