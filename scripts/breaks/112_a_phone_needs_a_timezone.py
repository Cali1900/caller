# A PHONE AND A TIMEZONE ARE SAVED TOGETHER OR NOT AT ALL.
#
# An imported lead has neither. Adding just the number makes it LOOK dialable - a
# phone, on a campaign, queued - while windows.PREFERENCE_WINDOW joins on
# l.timezone, so a NULL there matches no window and the lead is silently excluded
# forever with nothing saying why.
#
# THE ARCHIVE BUG'S EXACT SHAPE: a row that reads as workable and is structurally
# unreachable. That one cost two days to find because nothing in the UI or the
# logs said anything at all - the lead simply never came up.
TARGET = 'api/web.py'
EXPECT = 'test_the_contact_save_refuses_a_phone_without_a_timezone'
LABEL = 'save a phone with no timezone, leaving the lead permanently filtered'
OLD = """    if phone_e164 and not timezone:"""
NEW = """    if False:"""
