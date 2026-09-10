# REPUTATION BELONGS TO THE ADDRESS, NOT THE CAMPAIGN. Two drips sharing
# info@counselorai.io at 15/hour each would put 30/hour on one mailbox, and the
# mailbox is what a spam filter scores. Counting per campaign would make every
# cap a lie the moment a second drip existed.
# ⚠️ RETARGETED 2026-09-10: the mailbox is read from email_sends.from_email, not
# reached through the lead's campaign - that join went with drip_campaign_id, and
# was a guess anyway. Removing the predicate counts EVERY mailbox as one.
TARGET = 'api/drip.py'
EXPECT = 'test_the_caps_count_per_mailbox_not_per_campaign'
LABEL = 'count sends per campaign instead of per mailbox'
OLD = """         WHERE es.from_email = c.sender_email"""
NEW = """         WHERE true"""
