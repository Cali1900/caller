# FAIL CLOSED. An unexpected error is not permission to send. Letting the
# exception escape - or worse, defaulting to ok - turns any transient fault
# into mail going out unchecked.
TARGET = 'api/autosend.py'
EXPECT = 'test_an_exception_in_the_check_holds_rather_than_sends'
LABEL = 'let an error in the eligibility check mean SEND'
OLD = """        return {'ok': False,
                'reasons': [f'{HoldReason.CHECK_FAILED}: {type(exc).__name__}'],
                'domain_class': domain_class}"""
NEW = """        return {'ok': True, 'reasons': [], 'domain_class': domain_class}"""
