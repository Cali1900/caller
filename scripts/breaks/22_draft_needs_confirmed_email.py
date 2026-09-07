TARGET = 'api/drafts.py'
EXPECT = 'test_no_draft_without_a_CONFIRMED_email'
LABEL = 'draft to an UNCONFIRMED email (burns the sending domain)'
OLD = "            if not lead['dm_email'] or lead['dm_email_confirmed'] is not True:\n                return None"
NEW = "            if not lead['dm_email']:\n                return None"
