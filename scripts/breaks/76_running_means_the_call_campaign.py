# running() feeds the dialer, the version picker and the sender, and all three
# mean "the campaign that is dialing". Unscoped, it returns a running drip -
# and the dialer takes a drip's cap, spacing and windows without any error.
TARGET = 'api/campaigns.py'
EXPECT = 'test_a_running_drip_does_not_block_or_masquerade_as_the_call_campaign'
LABEL = 'let running() return a drip campaign'
OLD = """            cur.execute("SELECT * FROM campaign_configs "
                        "WHERE is_running AND type = 'call'")"""
NEW = """            cur.execute("SELECT * FROM campaign_configs "
                        "WHERE is_running")"""
