# A write that goes nowhere and says nothing. update() used to filter silently
# to CONFIG_FIELDS, so a new column not listed there meant the UI saved, the
# call returned a row, and the value never changed - the auto-send switch would
# have read as ON while the gate saw 'manual'.
TARGET = 'api/campaigns.py'
EXPECT = 'test_updating_an_unknown_campaign_field_is_refused_not_ignored'
LABEL = 'silently ignore unknown campaign fields again'
OLD = """    unknown = sorted(set(fields) - set(CONFIG_FIELDS))
    if unknown:"""
NEW = """    unknown = []
    if False:"""
