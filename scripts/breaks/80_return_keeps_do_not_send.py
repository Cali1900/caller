# The other list. bad_email was ONLY a status once, and the sweep rewrites
# status to 'new' - so a hard-bounced address came back fully sendable. The
# list is keyed on the ADDRESS precisely so the lead's return cannot reach it.
TARGET = 'api/archive.py'
EXPECT = 'test_the_return_does_not_clear_the_email_do_not_send_list'
LABEL = 'let the archive sweep clear the email do-not-send list'
OLD = """            rows = cur.fetchall()
            for r in rows:"""
NEW = """            rows = cur.fetchall()
            if rows:
                cur.execute(
                    \"\"\"DELETE FROM email_do_not_send WHERE email IN (
                           SELECT lower(btrim(dm_email)) FROM leads
                            WHERE lead_id = ANY(%s::uuid[])
                              AND dm_email IS NOT NULL)\"\"\",
                    ([str(r['lead_id']) for r in rows],))
            for r in rows:"""
