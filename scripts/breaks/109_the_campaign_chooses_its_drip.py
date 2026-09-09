# THE CALL CAMPAIGN DECIDES WHICH SEQUENCE FOLLOWS ITS EMAIL 1.
#
# only_drip() assigns only when exactly ONE drip runs, so it does not scale past
# one: the moment there are two - a call-sourced sequence and an imported one,
# for any reason at all - every call-sourced lead that got email 1 joined NO
# DRIP. Email 1 out, lead at 'emailed', nothing following up, and nothing saying
# so until somebody opened that lead.
#
# Falling back to only_drip() when a default IS set is the subtle version of the
# same fault: the lead lands on whichever drip happens to be running, which means
# the wrong COPY. The call-sourced opener says "your front desk pointed me your
# way" and that sentence is false for an imported lead.
TARGET = 'api/drip.py'
EXPECT = 'test_the_call_campaign_chooses_which_drip_its_leads_enter'
LABEL = 'ignore default_drip_id and use whatever drip is running'
OLD = """    want = (campaign or {}).get('default_drip_id')"""
NEW = """    want = None"""
