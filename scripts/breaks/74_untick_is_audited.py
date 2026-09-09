# A misclick must be undoable - but never SILENTLY. A lead whose reply quietly
# vanished is one somebody emails again without knowing why they should not,
# and the timeline would show nothing to explain it.
TARGET = 'api/stages.py'
EXPECT = 'test_unticking_clears_it_and_says_so_on_the_timeline'
LABEL = 'withdraw a reply record without saying so'
OLD = """            cur.execute(
                \"\"\"INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'reply', 'reply record WITHDRAWN by hand', %s)\"\"\","""
NEW = """            if False:
              cur.execute(
                \"\"\"INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s, 'reply', 'reply record WITHDRAWN by hand', %s)\"\"\","""
