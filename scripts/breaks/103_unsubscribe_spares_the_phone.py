# AN EMAIL UNSUBSCRIBE SUPPRESSES EMAIL ONLY.
#
# Someone who does not want our emails has not given up the right to be phoned
# about a case they asked about. The two lists mean different things and must
# never merge:
#
#   suppression        keyed on the PHONE. Compliance, statutory damages behind
#                      it. Nobody lifts it.
#   email_do_not_send  keyed on the ADDRESS. The mailbox is dead or unwanted.
#
# Writing an unsubscribe into suppression is the merge, and it is the one that
# cannot be undone: it silently removes a firm from all future calling on the
# strength of an email preference.
#
# A prose "take me off your list" in a reply IS broader - and that is a person's
# judgement at the DNC button, not this function's.
TARGET = 'api/drip.py'
EXPECT = 'test_an_unsubscribe_stops_email_and_leaves_the_phone_alone'
LABEL = 'let an email unsubscribe suppress the phone too'
OLD = """    if reason == 'unsubscribed' and row['dm_email']:
        archive.do_not_send(row['dm_email'], 'unsubscribed', f'drip:{by}')"""
NEW = """    if reason == 'unsubscribed' and row['dm_email']:
        archive.do_not_send(row['dm_email'], 'unsubscribed', f'drip:{by}')
        with db.get_conn() as _c:
            with _c.cursor() as _cur:
                _cur.execute(
                    \"\"\"INSERT INTO suppression (phone_e164, reason, source)
                       SELECT phone_e164, 'unsubscribed', 'drip'
                         FROM leads WHERE lead_id = %s
                       ON CONFLICT DO NOTHING\"\"\", (lead_id,))"""
