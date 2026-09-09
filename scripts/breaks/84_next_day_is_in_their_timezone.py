# 'next_day' means 09:00 tomorrow where THEY are. Computed in the server's
# zone it lands mid-afternoon for a west-coast firm, or before the window
# opens for an east-coast one - and the calling window then pushes it, so the
# retry silently arrives a day late.
TARGET = 'api/retry_ladder.py'
EXPECT = 'test_next_day_lands_in_the_called_partys_morning_not_ours'
LABEL = 'compute next_day in the server timezone for every lead'
OLD = """        return ("(((now() AT TIME ZONE l.timezone)::date + 1) + %s::time)"
                " AT TIME ZONE l.timezone", [NEXT_DAY_AT])"""
NEW = """        return ("((now()::date + 1) + %s::time) AT TIME ZONE 'UTC'",
                [NEXT_DAY_AT])"""
