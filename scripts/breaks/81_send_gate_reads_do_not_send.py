# Exclusion 6. A list nothing consults is not an exclusion, it is a table.
# The gate must ask it, or a bounced address is emailed again the moment the
# lead comes back out of archive.
TARGET = 'api/autosend.py'
EXPECT = 'test_a_returned_bad_email_lead_is_still_refused_by_the_send_gate'
LABEL = 'stop the send gate consulting the email do-not-send list'
OLD = """            from api import archive as _archive
            if _archive.is_do_not_send(email):
                reasons.append(HoldReason.DO_NOT_SEND)"""
NEW = """            pass"""
