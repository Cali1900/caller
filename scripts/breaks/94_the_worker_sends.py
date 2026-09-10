# THE WORKER MUST ACTUALLY CALL THE SENDERS.
#
# ⚠️ THIS BREAK WAS INVERTED ON 2026-09-09, and the reason is the point.
#
# It used to assert the OPPOSITE: that api/worker.py did NOT reference the
# sender. api/sender.py and api/autosend.py were complete and tested and nothing
# called sender.run_once(), which is a gap rather than a decision - so the break
# made closing it turn a named test red, forcing it to be done on purpose.
#
# The drip is now built and the switches exist, so it has been done on purpose:
# email 1 is gated by the call campaign's email_1_mode ('manual' by default) and
# a drip step by its own campaign's is_running. The worker calling the loops is
# now the CORRECT state, and the thing worth guarding is the reverse - a
# refactor that quietly drops the call, leaving two complete senders that never
# run and a drip that silently never advances.
#
# A drip that stops sending is invisible: no error, no failed send, just firms
# that never hear from us again. That is worse than a loud break.
TARGET = 'api/worker.py'
EXPECT = 'test_the_worker_runs_both_send_loops'
LABEL = 'drop the send tick, so no email ever goes out again'
# ⚠️ REPOINTED 2026-09-10 when pacing landed. The send tick became ONE email per
# jittered gap instead of a 50-lead drain, so the old anchor
# (`_safe('sender', sender.run_once, cfg)`) no longer exists. The DRIP call is
# now the right anchor: it is the loop that must never be quietly dropped,
# because a drip that stops advancing is invisible - no error, no failed send,
# just firms that never hear from us again.
OLD = """            d = _safe('drip', drip.run_once, cfg, 1)"""
NEW = """            d = None"""
