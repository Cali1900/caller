# ⚠️ A FORM THAT OFFERS A CONTROL THE HANDLER IGNORES.
#
# /upload-form has had a `kind` select ("a CALL list" / "an EMAIL list") and a drip
# picker since the email import was built, and read NEITHER. Every upload went
# through the CALL parser, so an email-only CSV had every row rejected for a missing
# phone - immediately after the screen offered to import it. upload_emails() was
# reachable only from the test suite.
#
# This is worse than a missing feature: the screen SAYS it can do the thing. A
# control that does nothing is a promise the code does not keep, and the person
# using it has no way to tell the difference from their side.
TARGET = 'api/web.py'
EXPECT = 'test_the_upload_form_honours_KIND_and_the_drip_picker'
LABEL = 'ignore the kind select, so every upload is a call list again'
OLD = """    if kind == 'email':"""
NEW = """    if False:"""
