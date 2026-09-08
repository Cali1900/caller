TARGET = 'api/dialer.py'
EXPECT = 'test_l2_is_never_a_dial_candidate'
LABEL = 'let L2 dial (we owe them an email and have not sent it)'
OLD = "STAGE_DIALABLE = \"AND l.stage = 'L1'\""
NEW = "STAGE_DIALABLE = ''"
