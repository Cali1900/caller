# is_running IS THE SWITCH, exactly as it is for a call campaign.
#
# Same rule as the dialer: creating or editing a campaign must never start
# sending, and stopping one must stop it. one_running_campaign is scoped to
# type='call' (migration 028) so many drips may run at once, which makes each
# drip's own is_running the only thing that can turn it off.
#
# Losing this means a stopped drip keeps mailing - and the screen says stopped.
TARGET = 'api/drip.py'
EXPECT = 'test_a_stopped_drip_campaign_sends_nothing'
LABEL = 'let a STOPPED drip keep sending'
OLD = """RUNNING_STOP = "AND c.is_running AND c.type = 'drip'\""""
NEW = """RUNNING_STOP = "AND c.type = 'drip'\""""
