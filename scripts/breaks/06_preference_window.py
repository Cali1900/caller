TARGET = 'api/windows.py'
EXPECT = 'test_disabling_the_weekday_stops_everything'
LABEL = 'remove the per-weekday preference window'
OLD = '''PREFERENCE_WINDOW = """
    AND EXISTS (
        SELECT 1 FROM dialing_windows w
         WHERE w.dow = EXTRACT(dow FROM now() AT TIME ZONE l.timezone)::int
           AND w.enabled
           AND (now() AT TIME ZONE l.timezone)::time
               BETWEEN w.start_time AND w.end_time
    )
"""'''
NEW = "PREFERENCE_WINDOW = ''"
