# create(**overrides) filters to CONFIG_FIELDS, and type is deliberately not
# in it - changing a campaign's type under live leads is not an edit. Routed
# through that filter, type is dropped silently and every drip is created as a
# call campaign. The only symptom is a second drip that refuses to start.
#
# This is the third time this exact shape has bitten: email_1_mode, then
# website on the CSV upload, now type.
TARGET = 'api/campaigns.py'
EXPECT = 'test_type_is_not_silently_dropped_by_the_override_filter'
LABEL = 'route campaign type through the CONFIG_FIELDS filter'
OLD = """        'name': name.strip(),
        'type': type,"""
NEW = """        'name': name.strip(),"""
