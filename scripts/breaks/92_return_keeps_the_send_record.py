# THE RETURN DESTROYS THE SEND GATE, SO THE SEND RECORD IS SNAPSHOTTED FIRST.
#
# Without this, "we emailed this firm in September and heard nothing" becomes
# unreconstructable at the exact moment the lead comes back looking fresh -
# emailed_at, emailed_by and the confirmation are all cleared by the same
# UPDATE, and the archive_reason goes with them.
#
# Six months on, a firm that ignored four emails is a different prospect from
# one that was never written to, and nothing else on the row can tell them
# apart once the gate is cleared.
TARGET = 'api/archive.py'
EXPECT = 'test_the_old_send_record_survives_the_return'
LABEL = 'clear the send gate without preserving the send record'
OLD = """            _snapshot_contacts(cur, claimed)"""
NEW = """            pass"""
