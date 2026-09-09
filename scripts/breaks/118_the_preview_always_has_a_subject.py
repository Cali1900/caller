# THE PREVIEW MUST WORK BEFORE ANYONE IS ON THE DRIP.
#
# Which is exactly when the copy is being written. Requiring a real lead meant
# the preview was unavailable at the only moment it mattered - and on a real
# database it was worse than "empty": a call list arrives as company + phone, so
# 1,085 of 1,087 leads had no address and the picker offered ONE option, the
# operator's own test lead.
#
# Falling back to the SAMPLE rather than to "whichever lead comes first" is the
# other half. An unknown or stale id must not silently render against a stranger:
# that is somebody else's data appearing in a preview with nothing saying so, and
# the endpoint reports real=false precisely so the page can say which it is.
TARGET = 'api/web.py'
EXPECT = 'test_an_unknown_lead_id_falls_back_to_the_example_not_a_stranger'
LABEL = 'return an empty preview instead of falling back to the example'
OLD = """    if lead is None:
        lead = dict(drafts_mod.SAMPLE_LEAD)
        real = False"""
NEW = """    if lead is None:
        return JSONResponse({'steps': {}, 'lead': {}})"""
