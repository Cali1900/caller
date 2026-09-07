TARGET = 'api/windows.py'
EXPECT = 'test_preference_window_can_narrow_but_never_widen'
LABEL = 'remove the TCPA 08:00-20:30 legal window'
OLD = '''LEGAL_WINDOW = """
    AND (now() AT TIME ZONE l.timezone)::time
        BETWEEN TIME '08:00' AND TIME '20:30'
"""'''
NEW = "LEGAL_WINDOW = ''"
