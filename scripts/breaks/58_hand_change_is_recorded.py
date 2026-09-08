# A hand correction must never be mistakable for an agent capture. Without the
# timeline row, a status Sean set looks exactly like one the scorer set - and
# the whole point of the override is that he can tell them apart afterwards.
TARGET = 'api/web.py'
EXPECT = 'test_the_change_lands_on_the_timeline_as_a_HAND_change'
LABEL = 'stop recording that a status change was made BY HAND'
OLD = """            cur.execute(
                \"\"\"INSERT INTO activity (lead_id, kind, summary, detail)
                   VALUES (%s,'status','status changed BY HAND',%s)\"\"\",
                (lead_id, f'{was} -> {status}  (by {changed_by or "operator"})'))"""
NEW = "            pass"
