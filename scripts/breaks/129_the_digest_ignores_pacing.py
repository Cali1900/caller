# ⚠️ THE DIGEST ANSWERS "WHAT IS SCHEDULED", NOT "WHAT CLEARS THE CAP".
#
# A cap shifts WHEN a mail goes out; it does not change whether it is coming. If
# upcoming() applied the pace layers, tomorrow's list would silently shrink to
# whatever fits in one cap - and that block is the mitigation for a reply gate
# that CANNOT fail closed. Under-reporting there means a firm that already
# replied gets another email because its name never appeared on the checkpoint.
TARGET = 'api/drip.py'
EXPECT = 'test_the_digest_still_shows_what_a_cap_is_holding'
LABEL = 'let the digest hide sends a cap is holding'
OLD = """            cur.execute(_build_select(clause, pacing=False),
                        {'h': within_hours})"""
NEW = """            cur.execute(_build_select(clause),
                        {'h': within_hours, 'op_tz': _op_tz()})"""
