# A lead is counted ONCE, at the strongest stage it reached. If the CASE stops
# being ordered strongest-first, a lead that reached demo_booked also matches
# the earlier arms and the forecast double-counts exactly the leads that matter
# most - inflating the number Sean would act on.
#
# Re-anchored: the stage expression now reads STATUS rather than deriving from
# clicks and timestamps.
TARGET = 'api/forecast.py'
EXPECT = 'test_won_weighs_with_demo_booked_not_as_a_fifth_stage'
LABEL = 'stop counting a lead at its strongest forecast stage'
OLD = """    CASE WHEN l.status IN ('won', 'demo_booked', 'demo_pending') THEN 'demo_booked'"""
NEW = """    CASE WHEN l.status IN ('demo_booked', 'demo_pending')        THEN 'demo_booked'"""
