# dm_email_confirmed IS A GATE, NOT A FACT ABOUT THE FIRM.
#
# It does not record that the address is good; it records that WE verified it.
# Six months on that verification is stale even when the address is unchanged,
# and it is read as a gate in three places: autosend.eligibility() exclusion 1,
# drafts.generate_for() (break 22) and stages.advance_to_l2().
#
# Leaving it TRUE across the return means the auto-send gate passes on a
# confirmation nobody performed this year - to an address at a firm that has
# had six months to change staff. The ADDRESS is kept; only the claim that it
# was checked is cleared.
TARGET = 'api/archive.py'
EXPECT = 'test_the_return_clears_the_confirmation_but_keeps_the_address'
LABEL = 'let a six-month-old confirmation survive the return'
OLD = """                          dm_email_confirmed = NULL,
"""
NEW = """"""
