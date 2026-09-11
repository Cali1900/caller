# ⚠️ THE CALIFORNIA / HAWAII CASE. STATUS CANNOT EXPRESS DESTINATION.
#
# A California drip and a Hawaii drip both accept `emailed`. Without the WIRING
# condition every California lead receives the Hawaii sequence too - the gate says
# WHETHER a lead is ready, and nothing says WHERE it goes.
#
# Geography, or which campaign sourced a lead, is not something `status` can carry
# and must not be made to: that is the one-field-two-jobs fault this codebase keeps
# paying for. "Dumb" meant the system does not get clever about CHOOSING; it did
# not mean route to everything that matches.
#
# Removing this also silently widens every drip to every lead that passes its gate,
# which is the kind of change that looks like nothing until two firms compare notes.
TARGET = 'api/drip.py'
EXPECT = 'test_the_SAME_lead_does_not_receive_a_drip_it_is_not_wired_to'
LABEL = 'let a lead receive every drip that accepts its status'
OLD = """        gate=GATE, wired=WIRED, due_now=due_clause,"""
NEW = """        gate=GATE, wired='', due_now=due_clause,"""
