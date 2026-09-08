# An uploaded list is usually sorted - alphabetically by firm, which clusters
# by region and by firm type. Taking it in list order means the first hundred
# calls are not a SAMPLE of the list, and the script gets tuned against a
# biased slice without anyone knowing it is biased.
#
# The first version of this break ordered by created_at, which is IDENTICAL for
# every lead inserted in one transaction - so the order was arbitrary rather
# than upload order and the test passed anyway. It reported GREEN, i.e. "not
# covered", which is the safe direction but told me nothing. A break has to
# reproduce the bug, not merely change the code.
TARGET = 'api/dialer.py'
EXPECT = 'test_fresh_leads_are_not_selected_in_upload_order'
LABEL = 'select fresh leads in list order again'
OLD = """              md5(l.lead_id::text)"""
NEW = """              l.phone_e164"""
