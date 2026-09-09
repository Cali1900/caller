# Archived is out of every working view, and the filter lives in _lead_query
# so the COUNT and the bulk "add all matching" inherit it. Moved to the page,
# "add all N matching" quietly pulls resting leads back onto a live campaign -
# undoing the archive at scale, in one click.
TARGET = 'api/web.py'
EXPECT = 'test_the_bulk_add_never_sweeps_an_archived_lead_onto_a_campaign'
LABEL = 'let archived leads back into the shared lead query'
OLD = """        where.append("l.status <> 'archived'")"""
NEW = """        pass"""
