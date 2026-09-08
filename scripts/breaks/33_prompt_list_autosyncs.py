# The picker sat EIGHT versions behind Retell because the only sync was a
# button. If the page stops pulling on load, the list silently rots again -
# and a stale list is worse than an empty one, because it looks complete.
TARGET = 'api/prompts.py'
EXPECT = 'test_the_stale_check_does_not_hammer_retell'
LABEL = 'stop syncing the prompt list on page load'
OLD = """    try:
        sync_versions(cfg, stage)
        ent.update(at=time.time(), error=None)"""
NEW = """    try:
        ent.update(at=time.time(), error=None)"""
