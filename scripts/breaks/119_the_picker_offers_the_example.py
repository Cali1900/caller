# THE EXAMPLE IS AN OPTION IN THE PICKER, NOT A SILENT LAST RESORT.
#
# Writing the copy happens BEFORE anyone is on the drip, so an empty picker means
# no preview at the only moment it matters. And on a real database "empty" was
# not even the bad case: a call list arrives as company + phone, so 1,085 of
# 1,087 leads had no address and the picker offered exactly ONE option - the
# operator's own test lead - which reads as "nothing here is set up".
#
# Removing the option leaves a <select> with nothing in it when the drip is new,
# so the browser posts no lead_id and the operator has no way to choose the
# example even though the endpoint would render it.
TARGET = 'api/templates/campaign.html'
EXPECT = 'test_the_preview_works_with_no_leads_at_all'
LABEL = 'drop the example from the picker, leaving it empty on a new drip'
OLD = """          {% if not preview_candidates %}
          <option value="{{ sample_id }}" selected>{{ sample_lead.company }} &mdash;
            {{ sample_lead.dm_name }} (example, not a real lead)</option>
          {% endif %}"""
NEW = """"""
