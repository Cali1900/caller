# ⚠️ A FIRM MID-SEQUENCE VANISHED THE MOMENT IT REPLIED.
#
# The roster filtered strictly on the gate, so a lead whose status left the
# accepted set disappeared from the page entirely - no row, no reason, nothing
# saying it had ever been there. That is the same invisibility every other guard
# in this system exists to prevent, arriving in a new place.
#
# It stays if this drip has SENT it something, marked stopped, with the status that
# removed it. A lead that never received anything and does not qualify still does
# not appear: there is nothing to show, and a roster of everyone who ever failed to
# qualify is noise.
TARGET = 'api/drip.py'
EXPECT = 'test_a_lead_that_no_longer_qualifies_STAYS_VISIBLE_as_stopped'
LABEL = 'let a firm vanish from the roster when its status changes'
OLD = """                 WHERE (l.status = ANY(gc2.accepted_statuses)
                        OR p.steps_sent IS NOT NULL)"""
NEW = """                 WHERE l.status = ANY(gc2.accepted_statuses)"""
