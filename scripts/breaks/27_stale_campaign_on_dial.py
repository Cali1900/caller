# The lead carries a campaign row captured at SELECTION time. Trusting it makes
# the in-transaction re-check - the whole reason that block exists - blind to a
# pause or a switch during an in-flight batch, and lets a lead claimed under A
# dial under B's cap, spacing and prompt version.
#
# The previous break replaced only the `cid = ...` line, so the next line
# (`campaign = campaigns.get(cid) if cid else None`) immediately overwrote the
# stale campaign it had just injected with None - which REFUSES, and the test
# passed. The break has to replace the whole re-read, or it does not restore
# the bug it is meant to describe.
TARGET = 'api/dialer.py'
EXPECT = 'test_a_switch_mid_flight_does_not_dial_the_lead_under_the_new_campaign'
LABEL = "trust the claimed lead's stale campaign instead of re-reading it"
OLD = """            with conn.cursor() as cur:
                cur.execute('SELECT campaign_id FROM leads WHERE lead_id = %s',
                            (lead['lead_id'],))
                row = cur.fetchone()
            cid = row and row['campaign_id']
            # No fallback to running(): "whatever is running now" is how a
            # lead claimed under A ends up dialed under B.
            campaign = campaigns.get(cid) if cid else None"""
NEW = """            campaign = lead.get('campaign') or campaigns.running()"""
