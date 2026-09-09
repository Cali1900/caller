# EMAIL 1 IS STEP 1, AND THIS WAS A REAL BUG FOUND WHILE BUILDING THE IMPORT.
#
# A call-sourced lead's email 1 is recorded with step_id NULL, because no drip
# existed when it went. ALREADY_SENT_STOP matches on step_id, so without linking
# it to the drip's step 1 that step is UNSENT and due immediately at delay 0:
# the firm receives the same opener twice, minutes apart.
#
# Linking makes both entry paths agree on what step 1 means:
#
#   call-sourced   email 1 IS step 1 -> next due is step 2, at its delay
#   imported       no email 1 -> step 1 is due now and starts the clock
TARGET = 'api/drip.py'
EXPECT = 'test_email_1_counts_as_step_1_for_a_call_sourced_lead'
LABEL = 'let a call-sourced lead get the opener twice'
OLD = """            WHERE es.lead_id = %s AND es.seq = 1 AND es.step_id IS NULL
              AND es.sent_at IS NOT NULL\"\"\","""
NEW = """            WHERE false AND es.lead_id = %s AND es.seq = 1\"\"\","""
