# The gap between selecting a lead and sending to it is REAL - they can reply,
# be marked DNC, or have the campaign switched to manual in between. Trusting
# the selection is the same bug dial_one had with its stale campaign snapshot.
TARGET = 'api/sender.py'
EXPECT = 'test_a_reply_between_selection_and_send_stops_it'
LABEL = 'trust selection instead of re-checking the gate at send time'
OLD = """                decision = autosend.eligibility(lead, camp)
                if not decision['ok']:"""
NEW = """                decision = {'ok': True, 'reasons': []}
                if False:"""
