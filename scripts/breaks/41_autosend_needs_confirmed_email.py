# EXCLUSION 1. An unconfirmed email is one the agent heard and nobody verified.
# Auto-sending to it is how a whole campaign lands in a spam trap on a
# mis-heard domain.
TARGET = 'api/autosend.py'
EXPECT = 'test_1_an_unconfirmed_email_is_held'
LABEL = 'auto-send to an UNCONFIRMED email address'
OLD = """        if lead.get('dm_email_confirmed') is not True:
            reasons.append(HoldReason.UNCONFIRMED)"""
NEW = "        pass"
