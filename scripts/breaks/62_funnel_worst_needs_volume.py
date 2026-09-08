# A funnel with almost nothing in it has not TESTED its steps. Naming a worst
# step there points at the wrong thing every time a campaign is new - and the
# whole value of the screen is that the highlighted step is where to look.
TARGET = 'api/funnel.py'
EXPECT = 'test_a_thin_funnel_names_no_worst_step'
LABEL = 'name a worst step on a funnel with no volume behind it'
OLD = """                  and (c.get(r['pct_of']) or 0) >= MIN_DENOM"""
NEW = """                  and True"""
