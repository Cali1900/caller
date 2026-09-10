# ⚠️ STOPPING A DRIP CHANGES WHAT HAPPENS TO EVERY FUTURE LEAD THAT FEEDS IT.
#
# drip_for() refuses to route a lead into a STOPPED drip - deliberately, so nobody
# lands on whatever copy happens to be running instead. The consequence is that
# stopping a drip silently converts every pointing campaign into "email 1 goes out
# and nothing follows", which is the exact state that took three asks to surface.
#
# Nothing is orphaned in the database and the wiring is kept, so this is a warning
# rather than a refusal. Without it the change is invisible: no error, no lead in
# any queue, just openers with no sequence behind them.
# ⚠️ RETARGETED 2026-09-10: nothing FEEDS a drip now, so the confirmation counts
# the leads that currently QUALIFY instead of the campaigns that pointed at it.
# Same consequence, measured by the mechanism that decides it.
TARGET = 'api/web.py'
# ⚠️ RENAMED WITH ITS TEST. The status gate made "fed by a call campaign"
# meaningless, so the test became test_stopping_a_drip_with_leads_asks_first - and
# this EXPECT was left naming the old one. The pass reported "no test named ...
# exists <-- not coverage", which is the right answer: a break naming a test that
# does not exist is a guard nothing verifies, and it would have sat here reading
# like coverage.
EXPECT = 'test_stopping_a_drip_with_leads_asks_first'
LABEL = 'stop a fed drip silently'
OLD = """    if row['type'] == 'drip' and confirm != 'yes':
        if _drip_mod.roster(campaign_id, limit=1):"""
NEW = """    if False:
        if False:"""
