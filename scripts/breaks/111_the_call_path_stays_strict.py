# THE SOURCE-AWARE EXCLUSIONS MUST NOT RELAX THE CALL PATH.
#
# autosend holds a lead whose email was not CONFIRMED BY THE AGENT and one with
# NO CONTACT NAME. Both substitute for a human verifying the contact, and for a
# call-sourced lead the evidence is a spellback on a recorded call.
#
# An imported lead has different evidence in kind - a person chose to upload the
# file - which is why lead_source exists rather than setting dm_email_confirmed
# on import. Treating EVERY lead as imported is the way that goes wrong: it
# silently drops both exclusions for call-sourced leads too, and a wrong email
# captured badly on a call would then be mailed automatically.
TARGET = 'api/autosend.py'
EXPECT = 'test_the_call_path_is_unchanged_by_source_awareness'
LABEL = 'treat every lead as imported, relaxing the call path too'
OLD = """        imported = lead.get('lead_source') == 'import'"""
NEW = """        imported = True"""
