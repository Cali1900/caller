TARGET = 'api/dialer.py'
EXPECT = 'test_pausing_stops_selection_immediately'
LABEL = 'let a PAUSED queue still produce candidates'
OLD = """    if not settings_mod.get('dialing_enabled'):
        return []"""
NEW = "    pass"
