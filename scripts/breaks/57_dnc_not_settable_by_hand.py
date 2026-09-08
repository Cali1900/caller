# Setting status='dnc' from the dropdown would leave a lead that LOOKS
# suppressed and is still dialable - the suppression row is what actually stops
# the call, and /dnc writes it in the same transaction. Suppression is the
# highest-liability object in this system; it does not get a shortcut.
TARGET = 'api/web.py'
EXPECT = 'test_dnc_cannot_be_set_from_the_dropdown'
LABEL = 'allow dnc to be set by hand without writing suppression'
OLD = """    if status not in MANUAL_STATUSES:"""
NEW = """    if False:"""
