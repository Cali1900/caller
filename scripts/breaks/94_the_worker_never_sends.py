# AUTO-SEND IS BUILT AND DELIBERATELY NOT WIRED.
#
# This break is the inverse of the usual shape: it does not remove a guard, it
# ADDS the one line that turns on automated outbound email to law firms. That
# is the whole risk - api/sender.py and api/autosend.py are complete and
# tested, so the only thing standing between "nothing auto-sends" and a worker
# mailing firms on a timer is a line nobody has written.
#
# A line nobody has written is not a decision, it is a gap, and the next person
# to notice sender.run_once() has no caller will read it as an oversight and
# close it. This makes closing it turn a named test red, so it has to be done
# on purpose - with reply detection in place, which the brief calls a HARD GATE
# and which is not built.
TARGET = 'api/worker.py'
EXPECT = 'test_the_worker_does_not_run_the_sender_loop'
LABEL = 'wire the auto-send loop into the worker'
OLD = """            r = _safe('archive.return_due', archive.return_due)"""
NEW = """            _safe('sender', sender.run_once, cfg)
            r = _safe('archive.return_due', archive.return_due)"""
