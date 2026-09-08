# The endpoint is PUBLIC - a recipient's browser hits it. Putting the lead id in
# the URL makes it enumerable and tells anyone holding one link who else was
# mailed. A token is 128 bits precisely so it cannot be walked.
TARGET = 'api/clicks.py'
EXPECT = 'test_the_url_carries_a_token_and_nothing_else'
LABEL = 'put the lead id in the public click URL'
OLD = """    tok = token_for(lead_id)
    return f"{base_url.rstrip('/')}/c/{tok}" if tok else None"""
NEW = """    tok = token_for(lead_id)
    return f"{base_url.rstrip('/')}/c/{lead_id}" if tok else None"""
