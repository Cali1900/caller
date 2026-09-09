# THE LOUD HALF. A lead reaching the dial path with no number is a DIFFERENT
# event from one being filtered out: something put it there, and "nothing
# happened" is the worst possible report.
#
# Without this, in unrestricted mode a NULL sails through assert_dialable
# (allowlist mode refuses it, but unrestricted returns early) and reaches
# retell.create_phone_call(to_number=None), which surfaces as a Retell API error
# and reads as a Retell problem rather than a bad lead.
#
# The refusal is audited as 'refused_no_phone' rather than 'refused_other', so
# dial_audit says which guard fired.
TARGET = 'api/guards.py'
EXPECT = 'test_the_pre_dial_guard_refuses_loudly_and_is_audited'
LABEL = 'dial a lead with no number instead of refusing'
OLD = """    phone = (lead or {}).get('phone_e164')
    if not (phone or '').strip():"""
NEW = """    phone = (lead or {}).get('phone_e164')
    if False:"""
