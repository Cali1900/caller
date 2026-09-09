# THE GUARD THE DRIP WILL READ. A person ticking "I got a reply" must stop
# every automatic contact for that lead - permanently. Four emails to somebody
# who already answered is the worst thing this system can do, and it is the
# most common way these systems fail.
#
# This is the same field automatic ingest will write when it lands, so this
# break guards both.
TARGET = 'api/stages.py'
EXPECT = 'test_a_recorded_reply_blocks_auto_send'
LABEL = 'record a reply WITHOUT stamping replied_at'
OLD = """                \"\"\"UPDATE leads SET replied_at = %s, status = 'engaged',
                          reply_note = NULLIF(%s,''), replied_by = %s,
                          updated_at = now()"""
NEW = """                \"\"\"UPDATE leads SET replied_at = NULL, status = 'engaged',
                          reply_note = NULLIF(%s,''), replied_by = %s,
                          updated_at = now()"""
