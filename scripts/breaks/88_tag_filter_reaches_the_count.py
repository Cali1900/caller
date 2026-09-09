# A filter the shared query cannot see means "Add all N matching" acts on a
# different set from the one on screen. That shipped once already, reading
# six filters of sixteen, and the confirm said "2 matching: state = NC"
# while it added everything.
TARGET = 'api/web.py'
EXPECT = 'test_the_tag_filter_reaches_the_count_and_the_bulk_add'
LABEL = 'drop the tag filter from the shared lead query'
OLD = """        where.append('EXISTS (SELECT 1 FROM lead_tags lt2 '
                     'WHERE lt2.lead_id = l.lead_id AND lt2.tag = %s)')
        params.append(_clean_tag(tag))"""
NEW = """        pass"""
