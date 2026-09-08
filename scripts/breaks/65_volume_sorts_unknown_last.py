# A firm that declined to answer is not a firm that sends no demands. Without
# NULLS LAST it sorts among the zeros, and the email queue Sean works by volume
# puts the firms he knows least about at the bottom next to the smallest ones -
# where they look the same.
TARGET = 'api/web.py'
EXPECT = 'test_the_list_sorts_by_volume_with_unknowns_last'
LABEL = 'sort unknown demand volume among the zeros'
OLD = """    'volume': 'l.demands_per_month DESC NULLS LAST, l.company',"""
NEW = """    'volume': 'l.demands_per_month DESC NULLS FIRST, l.company',"""
