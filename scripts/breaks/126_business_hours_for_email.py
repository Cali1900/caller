# BUSINESS HOURS, IN THE FIRM'S OWN TIMEZONE. Nobody legitimate mails a law firm
# at 3am, and a send at 3am local is a spam signal before anyone reads a word of
# it. Reuses windows.PREFERENCE_WINDOW rather than reimplementing it: two copies
# of a timezone rule are two rules, and the first time they disagree one of them
# is sending at 4am.
TARGET = 'api/drip.py'
EXPECT = 'test_a_step_outside_business_hours_waits'
LABEL = 'send at any hour of the day or night'
OLD = """        pacing=(pace_sql if pace_sql is not None
                else ((HOURLY_CAP + DAILY_CAP + _email_window())
                      if pacing else '')))"""
NEW = """        pacing=(pace_sql if pace_sql is not None
                else ((HOURLY_CAP + DAILY_CAP)
                      if pacing else '')))"""
