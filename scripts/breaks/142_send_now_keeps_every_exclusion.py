# ⚠️ SEND NOW SKIPS THE TIMING AND NOTHING ELSE.
#
# The step delay, business hours and the pace queue live in SELECTION. Every
# exclusion - do-not-send, suppression, no address, a recorded reply, the drip's
# own switch, the dev guard - lives inside send_step's transaction.
#
# So the override builds the row selection would have built and calls the SAME
# send_step. Bypassing it with a hand-rolled send is how an exclusion gets
# forgotten, and the one that gets forgotten is the one that mattered: this is the
# only control in the app that puts mail on the wire on demand.
#
# row_for_send() also refuses a step belonging to ANOTHER drip, so the override
# cannot reach outside the sequence it was opened from.
TARGET = 'api/drip.py'
EXPECT = 'test_send_now_skips_the_TIMING_and_nothing_else'
LABEL = 'let SEND NOW reach a step on a different drip'
OLD = """                  JOIN drip_steps s ON s.campaign_id = c.campaign_id
                                   AND s.step_id = %s AND s.deleted_at IS NULL"""
NEW = """                  JOIN drip_steps s ON s.step_id = %s
                                   AND s.deleted_at IS NULL"""
