# EXCLUSION 4. needs_human is the scorer saying a PERSON should look at this
# call. Auto-sending past it discards the one judgement the review queue exists
# to capture.
TARGET = 'api/autosend.py'
EXPECT = 'test_4_needs_human_is_held'
LABEL = 'auto-send a lead flagged for human review'
OLD = """        if lead.get('status') == 'human_review' or lead.get('needs_human'):
            reasons.append(HoldReason.NEEDS_HUMAN)"""
NEW = "        pass"
