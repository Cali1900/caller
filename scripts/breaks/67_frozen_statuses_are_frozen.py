# human_review is the scorer asking for a PERSON. Advancing past it silently
# clears the one flag that says "look at this". dnc is compliance. lost,
# lost_no_response and bad_email are conclusions - a stray click on a bounced
# address is not a reason to declare the lead engaged again.
TARGET = 'api/pipeline.py'
EXPECT = 'test_the_system_never_touches_a_frozen_status'
LABEL = 'let the system advance a lead out of human_review or DNC'
OLD = """    if current in FROZEN:
        return False"""
NEW = """    pass"""
