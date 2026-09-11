# ⚠️ THE CALIFORNIA / HAWAII CASE, ONE LAYER DOWN.
#
# Imported leads used to enter on the GATE ALONE, because an imported lead has no
# call campaign to be wired by. That carve-out had the same flaw the wiring exists
# to fix: import a California list and a Hawaii list - both `imported` - and every
# drip accepting `imported` received BOTH.
#
# The batch is the unit of the decision, made once at upload by a person. Removing
# the wiring condition here restores the carve-out and silently widens every drip to
# every imported lead that passes its gate - the kind of change that looks like
# nothing until two firms compare notes.
#
# NOT the assignment column returning: drip_campaign_id was "which drip owns this
# lead", a second fact free to disagree with status. This is WIRING only, and the
# gate still decides on every selection.
TARGET = 'api/drip.py'
EXPECT = 'test_two_imported_lists_do_not_both_go_to_every_drip'
LABEL = 'let every imported list reach every drip that accepts `imported`'
OLD = """         OR l.import_drip_id = c.campaign_id)
\"\"\""""
NEW = """         OR l.campaign_id IS NULL)
\"\"\""""
