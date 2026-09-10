# ONE STEP PER LEAD PER TICK.
#
# After a pause - a stopped drip, a worker outage, a sequence edited to bring
# delays forward - several steps can be due at the same instant. Sending them all
# puts two or three emails in front of one firm inside a minute, which reads as a
# malfunction and is the fastest way to get a sending domain blocked.
#
# The sequence must advance in ORDER and one rung at a time, exactly like the
# retry ladder. due() therefore returns the EARLIEST unsent due step per lead.
# ⚠️ RETARGETED 2026-09-10: the key became (lead, drip) rather than the lead
# alone, so a lead qualifying for two drips can receive both. WITHIN a drip the
# rule is unchanged and this still guards it: after a pause several steps can be
# due, and sending them all puts three emails in front of one firm.
TARGET = 'api/drip.py'
EXPECT = 'test_only_the_earliest_unsent_due_step_is_selected'
LABEL = 'send every due step at once after a pause'
# ⚠️ ANCHORED ON due()'s OWN execute LINE, not on the function that follows it.
# This definition has been repointed twice because it identified due() by whatever
# was defined next - first upcoming(), then held() - and adding a function above
# them broke it both times. The execute line is unique to due() and moves with it.
OLD = """                key = (r['lead_id'], r['drip_campaign_id'])
                if key in seen:
                    continue
                seen.add(key)"""
NEW = """                pass"""
