# ⚠️ WITHOUT THE LINK, A CALL-SOURCED LEAD'S SEQUENCE NEVER COMPLETES.
#
# ⚠️ AND THE OBVIOUS REASON IS THE WRONG ONE. I wrote this break believing the
# link prevented a duplicate opener. It does not: DUE_NOW already selects
# position 1 only when `emailed_at IS NULL`, so a lead that has had email 1 can
# never be picked for step 1 whatever the link says. The break reported GREEN and
# that is how the mistake surfaced.
#
# What the link actually carries is ATTRIBUTION and COMPLETION:
#
#   step_stats     email 1's send row has step_id NULL, so step 1 reads "0 sent"
#                  for every call-sourced lead and its click rate is a division by
#                  a denominator that excludes everyone who received it
#   _maybe_finish  counts done steps by joining email_sends to drip_steps. Step 1
#                  never counts, so `done < total` forever: the lead never
#                  finishes and sits in the drip permanently - the LIMBO the model
#                  explicitly refuses
#
# enter() did this once, at assignment. There is no assignment, so send_step()
# does it the first time step 1 is considered.
TARGET = 'api/drip.py'
EXPECT = 'test_email_1_is_attributed_to_step_1_so_the_sequence_can_finish'
LABEL = "leave email 1 unlinked, so step 1 never counts and nothing finishes"
OLD = """                if lead.get('emailed_at'):
                    _first = steps(row['drip_campaign_id'])
                    if _first:
                        link_email_1(cur, lead_id, _first[0]['step_id'])"""
NEW = """                pass"""
