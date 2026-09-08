# The Copy button copies the TEXTAREA, and the textarea renders draft.body.
# Sending anything other than draft.body means the tracked link can exist on
# one path and be missing from the other - and Sean would have no way to tell
# which email he actually sent.
TARGET = 'api/sender.py'
EXPECT = 'test_the_copy_path_and_the_send_path_carry_the_SAME_body'
LABEL = 'send something other than the draft body on SEND NOW'
OLD = """        result = mail.send(cfg, to_email, draft['subject'], draft['body'],
                           sender_email=camp.get('sender_email'),
                           sender_name=camp.get('sender_name'))

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if result.get('ok'):
                    _audit(cur, lead_id, to_email, 'sent_manual',"""
NEW = """        result = mail.send(cfg, to_email, draft['subject'],
                           draft['body'].replace('https://', 'hxxps://'),
                           sender_email=camp.get('sender_email'),
                           sender_name=camp.get('sender_name'))

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if result.get('ok'):
                    _audit(cur, lead_id, to_email, 'sent_manual',"""
