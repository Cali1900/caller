TARGET = 'api/digest.py'
EXPECT = 'test_a_success_is_never_labelled_a_top_failure'
LABEL = 'let a SUCCESS be listed as a top failure'
OLD = """                  AND NOT (s.what_happened = ANY(%s))
                GROUP BY s.what_happened ORDER BY n DESC LIMIT %s\"\"\",
            (tz, date, list(SUCCESS_OUTCOMES), TOP_FAILURES))"""
NEW = """                  AND (%s IS NOT NULL)
                GROUP BY s.what_happened ORDER BY n DESC LIMIT %s\"\"\",
            (tz, date, list(SUCCESS_OUTCOMES), TOP_FAILURES))"""
