TARGET = 'api/web.py'
EXPECT = 'test_dnc_writes_suppression_AND_status_in_one_transaction'
LABEL = 'mark DNC without writing the suppression row'
OLD = """            cur.execute(
                \"\"\"INSERT INTO suppression (phone_e164, reason, source)
                   SELECT phone_e164, 'requested', 'crm' FROM leads WHERE lead_id = %s
                   ON CONFLICT (phone_e164) DO NOTHING\"\"\", (lead_id,))"""
NEW = "            pass"
