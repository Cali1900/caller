# ⚠️ THE FAILURE THIS WHOLE SYSTEM EXISTS TO PREVENT.
#
# Suppression is keyed on the PHONE and outlives the lead. A sweep that
# "tidied up" on the way past - clearing what looks like stale state for a
# lead going back to the pool - would put a person who asked to be removed
# back in the dialer, six months later, with nothing on screen to say so.
TARGET = 'api/archive.py'
EXPECT = 'test_a_suppressed_number_returning_from_archive_is_still_not_dialable'
LABEL = 'let the archive sweep clear suppression on the way out'
OLD = """            rows = cur.fetchall()
            for r in rows:"""
NEW = """            rows = cur.fetchall()
            if rows:
                cur.execute(
                    \"\"\"DELETE FROM suppression WHERE phone_e164 IN (
                           SELECT phone_e164 FROM leads
                            WHERE lead_id = ANY(%s::uuid[]))\"\"\",
                    ([str(r['lead_id']) for r in rows],))
            for r in rows:"""
