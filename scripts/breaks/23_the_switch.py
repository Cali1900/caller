TARGET = 'api/guards.py'
EXPECT = 'test_pausing_blocks_a_lead_that_was_already_claimed'
LABEL = 'remove THE SWITCH (adding leads would start dialing)'
OLD = """    if not settings.get('dialing_enabled'):
        raise DialRefused('dialing is switched OFF - refusing')"""
NEW = "    return"
