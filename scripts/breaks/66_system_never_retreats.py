# THE ASYMMETRY: automation moves a lead FORWARD through stages it can observe;
# a person moves it anywhere. Without the rank check, a stray click drags a
# lead that already booked a demo back to `engaged` - and the forecast reweighs
# it from 40% to 15% on the strength of somebody re-opening an old email.
TARGET = 'api/pipeline.py'
EXPECT = 'test_a_click_never_drags_a_booked_lead_backwards'
LABEL = 'let an automatic advance move a lead BACKWARDS'
OLD = """    return RANK.get(current, 0) < RANK[target]"""
NEW = """    return True"""
