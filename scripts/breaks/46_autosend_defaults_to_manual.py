# THE SWITCH. Same rule as the dialer: turning a campaign on must never start
# sending. Defaulting to auto - or treating an unset mode as auto - means
# creating a campaign starts mailing strangers.
TARGET = 'api/autosend.py'
EXPECT = 'test_a_manual_campaign_never_auto_sends_however_clean_the_lead'
LABEL = 'treat a MANUAL campaign as auto'
OLD = """        if campaign.get('email_1_mode') != 'auto':"""
NEW = """        if False:"""
