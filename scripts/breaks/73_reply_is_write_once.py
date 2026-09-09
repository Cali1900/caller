# The FIRST reply is when they answered. Restamping would rewrite every
# "N days after" measurement resting on it - the same reason emailed_at is
# write-once. Undo is a separate, explicit, AUDITED operation, not a second
# tick that silently moves the clock.
TARGET = 'api/stages.py'
EXPECT = 'test_ticking_twice_does_not_restamp'
LABEL = 'let a second tick restamp the reply time'
OLD = """                    WHERE lead_id = %s AND replied_at IS NULL
                    RETURNING lead_id\"\"\",
                (when, (note or '').strip(), by, lead_id))"""
NEW = """                    WHERE lead_id = %s
                    RETURNING lead_id\"\"\",
                (when, (note or '').strip(), by, lead_id))"""
