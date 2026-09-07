TARGET = 'api/timezones.py'
EXPECT = 'test_split_states_use_the_dominant_zone_and_are_flagged'
LABEL = 'stop flagging split-zone states (a wrong timezone is a TCPA problem)'
OLD = "    if code in SPLIT_STATE_TZ:\n        return SPLIT_STATE_TZ[code], 'state', True"
NEW = "    if code in SPLIT_STATE_TZ:\n        return SPLIT_STATE_TZ[code], 'state', False"
