# X-Forwarded-For is CLIENT-CONTROLLED and the column is `inet`. Without
# validation a garbage header fails the whole insert and the click - the actual
# signal - vanishes, while the redirect still succeeds so nothing looks wrong.
TARGET = 'api/clicks.py'
EXPECT = 'test_a_garbage_forwarded_for_never_loses_the_click'
LABEL = 'let a garbage X-Forwarded-For discard the click'
OLD = """    ip = _clean_ip(ip)"""
NEW = """    pass"""
