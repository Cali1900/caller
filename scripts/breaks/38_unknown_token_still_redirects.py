# A 404 tells a scanner it guessed wrong, and a real recipient whose link got
# mangled by a mail client sees an error from us for doing nothing wrong.
# Tracking is our problem; their click is not.
TARGET = 'api/webhooks.py'
EXPECT = 'test_an_unknown_token_still_redirects_and_reveals_nothing'
LABEL = 'return 404 for an unknown click token'
OLD = """    return RedirectResponse(dest or clicks.DESTINATION, status_code=302)"""
NEW = """    if not dest:
        raise HTTPException(status_code=404, detail='unknown token')
    return RedirectResponse(dest, status_code=302)"""
