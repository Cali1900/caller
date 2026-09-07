TARGET = 'api/dialer.py'
EXPECT = 'test_suppressed_number_is_never_a_candidate'
LABEL = 'remove suppression from the selection query'
OLD = """SUPPRESSION_JOIN = (
    'AND NOT EXISTS (SELECT 1 FROM suppression s '
    'WHERE s.phone_e164 = l.phone_e164)'
)"""
NEW = "SUPPRESSION_JOIN = ''"
