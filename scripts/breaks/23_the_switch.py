TARGET = 'api/guards.py'
EXPECT = 'test_pausing_blocks_a_lead_that_was_already_claimed'
LABEL = 'remove THE SWITCH (a stopped campaign would still dial a claimed lead)'
OLD = """    if not campaign or not campaign.get('is_running'):
        raise DialRefused('no campaign is running - refusing')"""
NEW = "    return"
