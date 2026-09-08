# The send is CLAIMED by stamping emailed_at BEFORE the API call, and
# mark_emailed is write-once. Claiming afterwards means a crash between the
# call and the stamp sends a SECOND email to a real person. Losing one email
# and sending two are not equivalent failures.
TARGET = 'api/sender.py'
EXPECT = 'test_a_lead_is_never_sent_twice'
LABEL = 'claim the send AFTER the API call instead of before'
OLD = """        claimed = stages.mark_emailed(lead_id, emailed_by=f'auto:{cfg.SENDER_DOMAIN}')
        if claimed is None:"""
NEW = """        claimed = True
        if False:"""
