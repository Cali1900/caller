# ⚠️ THE PER-STEP TABLE IS THE WHOLE REASON TO RUN A SEQUENCE - it says which
# email is doing the work and which one to cut. It is only buildable because the
# click token is per SEND: email_sends is one row per (lead, step) with its own
# token, and email_clicks.send_id points back at it.
#
# Joining clicks on lead_id instead credits EVERY step of a lead with every click
# that lead ever made. Step 1 and step 4 would show the same number, the rates
# would all converge, and the table would read as plausible while being useless -
# the worst failure mode for a number somebody makes a decision on.
TARGET = 'api/drip.py'
EXPECT = 'test_the_per_step_table_attributes_clicks_to_the_RIGHT_step'
LABEL = 'credit every step with every click the lead made'
OLD = """                  LEFT JOIN email_clicks ec ON ec.send_id = es.send_id
                 WHERE s.campaign_id = %s AND s.deleted_at IS NULL"""
NEW = """                  LEFT JOIN email_clicks ec ON ec.lead_id = es.lead_id
                 WHERE s.campaign_id = %s AND s.deleted_at IS NULL"""
