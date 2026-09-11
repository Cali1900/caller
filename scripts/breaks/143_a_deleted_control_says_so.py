# ⚠️ A DELETED CONTROL IS INVISIBLE IN EXACTLY THE WAY A MISSING FEATURE IS.
#
# The default_drip_id selector stood on a call campaign's config page. The status
# gate replaced it, the card was removed, and the answer to "how do I point this
# campaign at a drip" ended up nowhere near where anyone stands when they ask it.
# Sean went looking for it and spent time on a screen that no longer exists - the
# second time that has happened.
#
# The note carries LIVE VALUES on purpose: which drips run, what each accepts, how
# many qualify, and a link to their leads. A note that only explains the concept
# would answer the question once; one that names the current state answers it every
# time, including the case that matters most - no drip running, so email 1 goes out
# and nothing follows it.
TARGET = 'api/templates/campaign.html'
EXPECT = 'test_a_call_campaign_says_what_replaced_the_drip_selector'
# ⚠️ THE BREAK ITSELF HAD TO BE FIXED. Its first version inserted an unbalanced
# `{% if false %}`, which made the template raise - so the test went red from a 500
# rather than from the missing note, and would have passed for the wrong reason if
# the note had been deleted while the markup stayed valid. A break that breaks
# something ELSE proves nothing about the guard it names.
#
# Emptying the loop keeps the template valid and removes exactly the live values.
LABEL = 'strip the live drip values from the follow-up note'
OLD = """        {% for d in running_drips %}"""
NEW = """        {% for d in [] %}"""
