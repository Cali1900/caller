# A lead that clicked AND booked a demo is ONE lead at its strongest stage. If
# the stage expression stops being ordered, the same lead is counted at two
# weights and the forecast double-counts exactly the leads that matter most.
TARGET = 'api/forecast.py'
EXPECT = 'test_a_lead_counts_once_at_its_strongest_stage'
LABEL = 'let a lead count at more than one forecast stage'
OLD = """    CASE WHEN l.status = 'demo_pending'                       THEN 'demo_booked'"""
NEW = """    CASE WHEN false                                           THEN 'demo_booked'"""
