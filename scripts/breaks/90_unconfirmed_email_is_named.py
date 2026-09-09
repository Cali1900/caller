# An unconfirmed email is the REASON a lead sits at L1 with an address on it
# and nothing sends. Reading "gave their email" makes the held send look like
# a bug in the sender rather than a lead waiting on one click.
TARGET = 'api/why.py'
EXPECT = 'test_an_unconfirmed_email_is_called_unconfirmed'
LABEL = 'report an unconfirmed email as captured'
OLD = """    if not row.get('dm_email_confirmed'):"""
NEW = """    if False:"""
