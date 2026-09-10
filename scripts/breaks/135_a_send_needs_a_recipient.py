# ⚠️ A SEND ROW WITHOUT A RECIPIENT IS NOT A SEND.
#
# record_send wrote `to_email or ''`, the same coercion migration 036 made to
# satisfy NOT NULL - and that produced rows claiming a send to nobody, one linked
# to a drip step, which the roster then counted as a step delivered. The lead had
# had nothing.
#
# The CHECK in the database is what makes it impossible; this refusal is what
# makes it LEGIBLE, naming the lead instead of surfacing a constraint violation
# from three layers down. Removing it does not let the row through - it turns a
# clear refusal into a CheckViolation traceback at the worst moment.
TARGET = 'api/drip.py'
EXPECT = 'test_record_send_refuses_a_lead_with_no_address_by_name'
LABEL = "coerce a missing address to '' again"
OLD = """    if not (to_email or '').strip():
        raise ValueError(f'lead {lead_id} has no email address - a send row '
                         f'without a recipient is not a send')"""
NEW = """    to_email = to_email or ''"""
