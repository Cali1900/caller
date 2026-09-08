# L3's automatic follow-up is unwired: a follow-up is its own campaign. If L3
# creeps back into the dialable set, an L3 lead sitting in the queue gets
# dialed by the L1 campaign - wrong prompt, wrong cap, and a warm follow-up
# delivered as a cold call.
TARGET = 'api/dialer.py'
EXPECT = 'test_only_l1_is_a_dial_candidate'
LABEL = 'make L3 leads dialable again by the L1 campaign'
OLD = """STAGE_DIALABLE = "AND l.stage = 'L1'\""""
NEW = """STAGE_DIALABLE = "AND l.stage IN ('L1', 'L3')\""""
