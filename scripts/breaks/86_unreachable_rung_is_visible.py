# A rung only fires if another attempt follows it, so a four-rung ladder
# under max attempts 4 has a fourth rung that is decoration. Reporting every
# rung as reachable makes an editable value that does nothing look like a
# working setting - the fault that had deploy.sh reporting a pause it was
# not performing.
TARGET = 'api/retry_ladder.py'
EXPECT = 'test_a_rung_only_counts_if_an_attempt_follows_it'
LABEL = 'report every rung as reachable regardless of max attempts'
OLD = """    return max(0, min(len(ladder or []), max(0, max_attempts - 1)))"""
NEW = """    return len(ladder or [])"""
