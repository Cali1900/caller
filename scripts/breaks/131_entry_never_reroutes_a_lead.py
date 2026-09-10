# ⚠️ A CONFIG CHANGE MUST NOT YANK A LEAD MID-SEQUENCE INTO DIFFERENT COPY.
#
# `WHERE drip_campaign_id IS NULL` is what makes changing a call campaign's
# follow-up drip affect ONLY leads entering afterwards. Without it, entry would
# overwrite the drip of a lead already part-way through a sequence: it would
# jump to whichever step of the NEW sequence it had not been sent, mid-thread,
# with copy written for a different conversation - and drip_entered_at would be
# re-stamped, so step 1's timing would restart too.
#
# Nothing would error. The firm would just start receiving a different sequence.
TARGET = 'api/drip.py'
EXPECT = 'test_changing_the_follow_up_drip_does_not_move_leads_already_on_one'
LABEL = 'let entry re-route a lead already on a drip'
OLD = """            WHERE lead_id = %s AND drip_campaign_id IS NULL"""
NEW = """            WHERE lead_id = %s"""
