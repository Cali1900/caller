# ONLY THE SAMPLE LINK IS TRACKED, and it is tracked because the copy SAYS SO.
#
# This break restores the old behaviour: sweep every counselorai.io URL in the
# body. With one link in the copy that is invisible - which is why it shipped -
# but the moment a signature carries https://counselorai.io, a click on "find
# us here" is recorded as interest in the sample.
#
# Explicit beats positional: the copy can be reordered and carry any number of
# other links, and only {{sample_link}} is rewritten.
TARGET = 'api/drafts.py'
EXPECT = 'test_a_signature_link_is_left_alone'
LABEL = 'sweep every counselorai.io link again, signature included'
OLD = """    out = out.replace('{{' + k + '}}', str(values.get(k, '') or ''))
    return out"""
NEW = """    out = out.replace('{{' + k + '}}', str(values.get(k, '') or ''))
    # THE OLD SWEEP: every counselorai.io link, signature included.
    import re as _re
    tracked = values.get('sample_link') or ''
    if '/c/' in tracked:
        out = _re.sub(r'https?://(?:www\\.)?counselorai\\.io[^\\s]*', tracked, out)
    return out"""
