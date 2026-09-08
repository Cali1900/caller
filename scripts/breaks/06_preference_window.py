TARGET = 'api/windows.py'
EXPECT = 'test_disabling_the_weekday_stops_everything'
LABEL = 'ignore the campaign calling window entirely'
OLD = """    AND EXISTS (
        SELECT 1 FROM campaign_windows w
         WHERE w.campaign_id = l.campaign_id
           AND w.dow = EXTRACT(dow FROM now() AT TIME ZONE l.timezone)::int
           AND w.enabled
           AND (now() AT TIME ZONE l.timezone)::time
               BETWEEN w.start_time AND w.end_time
    )"""
NEW = "    AND true"
