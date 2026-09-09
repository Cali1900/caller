# The bug this replaced: BACKOFF was flat, so busy 15m and no_answer 2h
# whatever the attempt. Four attempts meant four calls to one firm inside
# eight hours - a pattern a receptionist notices, and the opposite of what
# the spacing work was for. Pinning to rung 1 restores exactly that.
TARGET = 'api/retry_ladder.py'
EXPECT = 'test_the_gap_grows_with_every_attempt'
LABEL = 'flatten the ladder back to its first rung'
OLD = """    i = min(max(attempts, 1), len(rungs)) - 1
    return rungs[i]"""
NEW = """    return rungs[0]"""
