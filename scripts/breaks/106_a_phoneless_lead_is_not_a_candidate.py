# ⚠️ THIS REPLACES A SCHEMA GUARANTEE.
#
# leads.phone_e164 was NOT NULL until migration 038, so a lead without a number
# COULD NOT EXIST and therefore could not be dialled. Email-only leads made it
# nullable, and this filter is half of what took the schema's place.
#
# It excludes SILENTLY, which is right for a filter - a lead that is not a
# candidate is not an event. guards.assert_has_phone is the loud half, for a lead
# that reaches the dial path anyway (break 107). Belt and braces, the same shape
# as suppression being in the join AND re-checked in the dial transaction.
#
# NOT keyed on lead_source: provenance is not the gate. An imported lead with a
# number added by hand IS dialable - see
# test_an_imported_lead_with_a_phone_added_by_hand_dials.
TARGET = 'api/dialer.py'
EXPECT = 'test_a_phoneless_lead_is_never_a_candidate'
LABEL = 'let an email-only lead become a dial candidate'
OLD = """PHONE_REQUIRED = 'AND l.phone_e164 IS NOT NULL'"""
NEW = """PHONE_REQUIRED = ''"""
