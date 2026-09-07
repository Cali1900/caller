TARGET = 'api/guards.py'
EXPECT = 'test_hard_cap_refuses_fresh_at_dial_time'
LABEL = 'remove the daily cap check'
OLD = """    if row['dialed'] >= row['daily_cap']:
        raise DialRefused(
            f'daily cap reached ({row["dialed"]}/{row["daily_cap"]}) - refusing fresh'
        )"""
NEW = "    return"
