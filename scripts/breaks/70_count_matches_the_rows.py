# The count query and the list query share ONE join clause. They have drifted
# three times - the clicks join, the campaign join, the score/email laterals -
# and each time the symptom was a 500 that read as "no matches", or a header
# count that silently disagreed with the table.
TARGET = 'api/web.py'
EXPECT = 'test_every_filter_and_sort_renders'
LABEL = 'let the count query drift from the list query again'
OLD = """                f\"\"\"SELECT count(*) AS n {_LEAD_JOINS}
                    WHERE {' AND '.join(where)}\"\"\","""
NEW = """                f\"\"\"SELECT count(*) AS n FROM leads l
                     LEFT JOIN email_drafts d ON d.lead_id = l.lead_id
                    WHERE {' AND '.join(where)}\"\"\","""
