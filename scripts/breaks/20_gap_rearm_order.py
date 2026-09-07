TARGET = 'api/worker.py'
EXPECT = 'test_the_worker_rearms_the_gap_before_dialing_not_after'
LABEL = 'arm the next gap AFTER the call instead of before (outcome-driven spacing)'
OLD = """            last_dial = now
            n = _safe('dialer', dialer.run_once, cfg)"""
NEW = """            n = _safe('dialer', dialer.run_once, cfg)
            last_dial = now"""
