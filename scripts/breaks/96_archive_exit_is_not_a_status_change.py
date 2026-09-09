# LEAVING THE ARCHIVE IS NOT A STATUS CHANGE.
#
# Without this refusal, the dropdown runs a bare UPDATE on an archived lead.
# That leaves pool_status='done', campaign_id NULL, archived_at/returns_at set,
# and every gate the return exists to clear still set: un-archived in name
# only, undialable and un-emailable.
#
# Worse, it strands the lead PERMANENTLY in two independent ways:
#
#   * archive.return_due() selects WHERE status='archived', so the nightly
#     sweep can never see it again
#   * lead.html renders "Return to the pool now" only for an archived lead, so
#     the one control that would repair it disappears from the page
#
# No route back but hand SQL - and nothing on the screen says so.
#
# It refuses rather than quietly performing the restore, because unarchive()
# snapshots the send record and clears four gates, which is a great deal more
# than "set the status". A dropdown that silently did all that would make the
# timeline lie about what a person did. Same discipline as /dnc being the only
# route to suppression.
TARGET = 'api/web.py'
EXPECT = 'test_the_status_dropdown_refuses_to_take_a_lead_out_of_archive'
LABEL = 'let the status dropdown strand a lead on its way out of archive'
OLD = """            if was == 'archived':"""
NEW = """            if False:"""
