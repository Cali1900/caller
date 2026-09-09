# ⚠️ HTML FORMS CANNOT NEST, AND THIS MADE THE EDITOR COMPLETELY UNUSABLE.
#
# The sequence <form> sat inside the campaign-config <form>. The browser drops
# the inner one, so "Save the sequence" submitted the OUTER form to
# /campaign/{id}/save - which requires agent_l1_version, daily_cap,
# max_concurrent and the spacing fields. A DRIP CAMPAIGN HAS NONE OF THEM, so it
# 422'd with a list of missing call-only fields and no step could be added at
# all.
#
# Every test written for the editor passed, because they all asserted the markup
# was PRESENT. Presence is not function. The bug was found by opening the page
# and clicking the button, which is the check those tests cannot replace - and
# the one that had been flagged as missing and then not done.
#
# This break re-nests the form. The named test asserts the config form CLOSES
# before the sequence form opens, so it goes red on the structure rather than on
# a symptom.
TARGET = 'api/templates/campaign.html'
EXPECT = 'test_the_sequence_form_is_not_nested_inside_the_config_form'
LABEL = 'nest the sequence form inside the config form again'
OLD = """<p><button type="submit" class="gold">Save campaign</button>
   <span class="dim">Config only &mdash; saving never starts or stops anything.</span></p>
</form>"""
NEW = """<p><button type="submit" class="gold">Save campaign</button>
   <span class="dim">Config only &mdash; saving never starts or stops anything.</span></p>"""
