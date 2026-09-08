# SEND NOW is pressed by a person, and a person pressing a button is not a
# reason to weaken the dev guard. Without this, a click in the CRM mails a real
# law firm from a dev box - the email equivalent of dialing a stranger.
TARGET = 'api/sender.py'
EXPECT = 'test_send_now_still_obeys_the_dev_allowlist'
LABEL = 'let SEND NOW bypass the email allowlist'
OLD = """                try:
                    guards.assert_emailable(to_email, cfg)
                except EmailRefused as exc:
                    _audit(cur, lead_id, to_email, 'refused_allowlist', str(exc))
                    return {'sent': False, 'detail': str(exc)}

                draft = drafts.get(lead_id)
                if not draft:
                    _audit(cur, lead_id, to_email, 'refused_no_draft', 'no draft')
                    return {'sent': False, 'detail': 'no draft to send'}"""
NEW = """                draft = drafts.get(lead_id)
                if not draft:
                    _audit(cur, lead_id, to_email, 'refused_no_draft', 'no draft')
                    return {'sent': False, 'detail': 'no draft to send'}"""
