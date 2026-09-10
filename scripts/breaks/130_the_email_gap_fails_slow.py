# FAILS SLOW, NEVER FAST - the same rule as FALLBACK_GAP for dials. When no drip
# is running, or the campaign row cannot be read, we do not know the configured
# spacing. An outage must never be able to TIGHTEN the interval between sends,
# because the failure mode of guessing low is a burst nobody chose.
TARGET = 'api/worker.py'
EXPECT = 'test_the_gap_falls_back_WIDE_when_no_drip_is_running'
LABEL = 'fall back to a tight gap when nothing is configured'
OLD = """FALLBACK_EMAIL_GAP = (60, 300)"""
NEW = """FALLBACK_EMAIL_GAP = (1, 2)"""
