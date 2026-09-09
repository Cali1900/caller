# A ladder whose waits shrink calls a firm MORE often the longer they have
# ignored us. Saved silently it looks like a working setting and reads on the
# screen as the ladder the operator typed.
TARGET = 'api/retry_ladder.py'
EXPECT = 'test_a_ladder_that_goes_backwards_is_refused'
LABEL = 'accept a retry ladder that goes backwards'
OLD = """        if m < prev:
            raise BadLadder("""
NEW = """        if False:
            raise BadLadder("""
