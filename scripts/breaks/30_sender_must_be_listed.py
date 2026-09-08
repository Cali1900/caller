# Free text let you save a from-address Brevo has never heard of - a config
# that cannot send. The dropdown is the UI half; this validation is the half
# that actually enforces it, because a POST does not have to come from the form.
TARGET = 'api/web.py'
EXPECT = 'test_the_save_route_rejects_an_unlisted_sender'
LABEL = 'accept any typed from-address again'
OLD = """    if not senders_mod.is_allowed(_cfg(), sender_email):"""
NEW = """    if False:"""
