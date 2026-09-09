# The bulk-add confirm is the last thing between a mis-set filter and 1,100
# leads on the wrong campaign. Built from its own list it named six filters
# of seventeen and read "status: new" while state and city were also active.
TARGET = 'api/web.py'
EXPECT = 'test_the_confirm_names_every_filter_not_just_the_first_six'
LABEL = 'describe only the first few filters in the bulk-add confirm'
OLD = """    return ', '.join(c['label'] for c in chips) or 'NO FILTER - every lead'"""
NEW = """    return ', '.join(c['label'] for c in chips[:1]) or 'NO FILTER - every lead'"""
