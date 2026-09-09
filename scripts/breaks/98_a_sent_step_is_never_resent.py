# A STEP ALREADY SENT IS NEVER RE-SENT AND NEVER RE-DATED.
#
# This one fragment is what makes editing a live sequence safe. Every rule in the
# brief about mid-flight edits falls out of it rather than being coded case by
# case: editing copy touches only leads who have not reached that step, changing
# a delay reschedules only them, deleting a step leaves those who got it with the
# record, and inserting one does not send anybody backwards.
#
# Without it the drip re-sends the whole sequence to everyone on every tick.
TARGET = 'api/drip.py'
EXPECT = 'test_editing_copy_does_not_resend_a_step'
LABEL = 'forget which steps a lead has already had'
OLD = """ALREADY_SENT_STOP = ('AND NOT EXISTS (SELECT 1 FROM email_sends es '
                     'WHERE es.lead_id = l.lead_id AND es.step_id = s.step_id)')"""
NEW = """ALREADY_SENT_STOP = ''"""
