# Comparing a timestamptz against a bare ::date applies the SESSION timezone
# (UTC), so between 17:00 Pacific and midnight UTC the digest for "today"
# silently omitted the evening's calls.
TARGET = 'api/digest.py'
EXPECT = 'test_the_digest_day_is_the_operators_day_not_utcs'
LABEL = 'compare the digest day in UTC instead of the operator timezone'
OLD = """              WHERE (c.created_at AT TIME ZONE %s)::date = %s::date\"\"\",
            (tz, date))
        return cur.fetchone()"""
NEW = """              WHERE %s IS NOT NULL
                AND c.created_at >= %s::date
                AND c.created_at < (%s::date + 1)\"\"\",
            (tz, date, date))
        return cur.fetchone()"""
