# The whole point of item 7. Searching a firm is how Sean asks "where is this
# and why"; a result he has to click through has not answered it. Dropping
# the line from the list leaves lead detail correct and the screen he reads
# first silent - which is exactly how it would rot unnoticed.
TARGET = 'api/web.py'
EXPECT = 'test_the_same_line_renders_on_the_list_and_on_lead_detail'
LABEL = 'stop rendering the why line on the leads list'
OLD = """            for r in rows:
                r['why'] = _why.line(r)"""
NEW = """            pass"""
