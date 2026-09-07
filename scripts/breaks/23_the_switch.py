TARGET = 'api/guards.py'
EXPECT = 'test_queueing_five_hundred_leads_places_zero_calls'
LABEL = 'remove THE SWITCH (adding leads would start dialing)'
OLD = """    if not settings.get('dialing_enabled'):
        raise DialRefused('dialing is switched OFF - refusing')"""
NEW = "    return"
