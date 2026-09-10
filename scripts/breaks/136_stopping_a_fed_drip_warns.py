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
TARGET = 'api/web.py'
EXPECT = 'test_stopping_a_fed_drip_asks_first'
LABEL = 'stop a fed drip silently'
OLD = """    if row['type'] == 'drip' and confirm != 'yes':
        fed = campaigns.feeders(campaign_id)
        if fed:
            return RedirectResponse(
                f'/campaign/{campaign_id}?stop_confirm=1', status_code=303)"""
NEW = """    pass"""
