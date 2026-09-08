# "If the save fails, say so rather than silently reverting." A refused save
# that reports success looks identical to one that worked and then reverted -
# and the operator goes hunting for a bug in the wrong place. Sean reported the
# picker as broken partly on this suspicion.
#
# The first version of this break anchored on the GENERIC fallback branch,
# which the named test never reaches - it hits the specific version-range
# branch. The break reported GREEN, i.e. "not covered", which is the safe
# direction but told me nothing. A break must remove the path the test walks.
TARGET = 'api/web.py'
EXPECT = 'test_a_refused_save_says_so_and_changes_nothing'
LABEL = 'report a REFUSED campaign save as if it had succeeded'
OLD = """        text = str(exc)
        if 'agent_l1_version_check' in text:"""
NEW = """        text = str(exc)
        msg = 'saved'
        if False:"""
