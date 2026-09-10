# REPUTATION BELONGS TO THE ADDRESS, NOT THE CAMPAIGN. Two drips sharing
# info@counselorai.io at 15/hour each would put 30/hour on one mailbox, and the
# mailbox is what a spam filter scores. Counting per campaign would make every
# cap a lie the moment a second drip existed.
TARGET = 'api/drip.py'
EXPECT = 'test_the_caps_count_per_mailbox_not_per_campaign'
LABEL = 'count sends per campaign instead of per mailbox'
OLD = """         WHERE mc.sender_email = c.sender_email"""
NEW = """         WHERE mc.campaign_id = c.campaign_id"""
