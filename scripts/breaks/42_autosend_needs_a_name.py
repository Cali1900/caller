# EXCLUSION 2. Without a name the copy opens "Hi ," or "Hi there" to a partner
# at a law firm. The template is built around having the name.
TARGET = 'api/autosend.py'
EXPECT = 'test_2_no_contact_name_is_held'
LABEL = 'auto-send with NO contact name captured'
OLD = """        if not imported and not (lead.get('dm_name') or '').strip():
            reasons.append(HoldReason.NO_NAME)"""
NEW = "        pass"
