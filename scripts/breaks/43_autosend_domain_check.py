# EXCLUSION 3, the one Sean said matters. A domain that is neither the firm's
# website nor known free-mail is a typo or a guess - bob@gmial.com confirmed by
# a receptionist saying "yes that's right".
TARGET = 'api/autosend.py'
EXPECT = 'test_3_a_domain_that_is_neither_site_nor_freemail_is_held'
LABEL = 'auto-send to a domain that matches neither the site nor free-mail'
OLD = """            v = validate(email, lead.get('website'))
            domain_class = v.get('domain_class')
            reasons.extend(v.get('reasons') or [])"""
NEW = """            v = validate(email, lead.get('website'))
            domain_class = v.get('domain_class')"""
