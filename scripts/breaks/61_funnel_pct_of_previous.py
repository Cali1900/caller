# The percentage must be OF THE PREVIOUS STEP. Of the top, every late step
# looks catastrophic and the one that is actually leaking disappears into a
# column of single digits - which is the opposite of what the screen is for.
TARGET = 'api/funnel.py'
EXPECT = 'test_the_percentages_are_of_the_previous_step'
LABEL = 'compute funnel percentages against the top instead of the previous step'
OLD = """        if denom_key:
            d = c.get(denom_key) or 0"""
NEW = """        if denom_key:
            d = c.get('in_queue') or 0"""
