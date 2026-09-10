# ⚠️ THE GATE IS THE WHOLE MECHANISM, and this break replaces the one that
# guarded default_drip_id routing.
#
# Membership is DERIVED from status. Removing the gate makes every drip accept
# every lead: a firm at `won`, a firm that said `dnc`, a firm mid-call - all of
# them selected for every running sequence. It is the single predicate standing
# between "one fact decides who gets mail" and "everyone gets everything".
#
# The old routing model stored drip_campaign_id at email-1 time, and status and
# membership could disagree - a lead marked `engaged` stayed queued because
# nothing consulted the status. That is the disagreement this predicate makes
# impossible rather than guarded against.
TARGET = 'api/drip.py'
EXPECT = 'test_an_engaged_lead_is_not_selected_by_a_drip_accepting_emailed'
LABEL = 'let every drip accept every lead'
OLD = """GATE = 'AND l.status = ANY(c.accepted_statuses)'"""
NEW = """GATE = ''"""
