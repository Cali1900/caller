TARGET = 'api/dialer.py'
EXPECT = 'test_selection_ignores_an_unstarted_campaign'
LABEL = 'remove the started/not-paused campaign gate'
OLD = """     AND c.started_at IS NOT NULL
     AND NOT c.paused"""
NEW = ""
