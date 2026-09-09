# "She did not answer" is an absence, not a zero. Without NULLS LAST it sorts
# among the smallest firms, and the queue Sean works by volume puts the firms
# he knows least about next to the ones he wants least - where they look the
# same.
#
# Re-anchored: sorting became column + direction, with NULLS LAST applied in
# _order_by for EVERY column and BOTH directions.
TARGET = 'api/web.py'
EXPECT = 'test_unknowns_sort_last_in_BOTH_directions'
LABEL = 'let unknown values sort among the real ones'
OLD = """    return f'{col} {d} NULLS LAST, l.company ASC, l.lead_id ASC'"""
NEW = """    return f'{col} {d}, l.company ASC, l.lead_id ASC'"""
