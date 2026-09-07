TARGET = 'api/stages.py'
EXPECT = 'test_an_unconfirmed_email_does_not_advance'
LABEL = 'advance the ladder on an UNCONFIRMED email'
OLD = """              AND dm_email IS NOT NULL
              AND dm_email_confirmed IS TRUE"""
NEW = "              AND dm_email IS NOT NULL"
