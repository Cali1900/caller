TARGET = 'api/digest.py'
EXPECT = 'test_a_success_is_never_labelled_a_top_failure'
LABEL = 'let successes be reported as TOP FAILURE'
OLD = """                  AND NOT (s.what_happened = ANY(%s))
                GROUP BY s.what_happened ORDER BY n DESC LIMIT %s\"\"\",
            (date, date, list(SUCCESS_OUTCOMES), TOP_FAILURES))"""
NEW = """                GROUP BY s.what_happened ORDER BY n DESC LIMIT %s\"\"\",
            (date, date, TOP_FAILURES))"""
