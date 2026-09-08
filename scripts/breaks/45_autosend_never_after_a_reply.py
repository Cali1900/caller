# EXCLUSION 5. A lead can carry replied_at from earlier work. Emailing someone
# who already answered us is the single worst thing this system can do, and it
# is the most common way these systems fail.
TARGET = 'api/autosend.py'
EXPECT = 'test_5_a_lead_that_already_replied_is_held'
LABEL = 'auto-send to a lead that ALREADY REPLIED'
OLD = """        if lead.get('replied_at') is not None:
            reasons.append(HoldReason.ALREADY_REPLIED)"""
NEW = "        pass"
