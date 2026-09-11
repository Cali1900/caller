# ⚠️ AN IMPORTED LEAD HAS NO CALL CAMPAIGN, SO NOTHING CAN WIRE IT.
#
# Wiring is a property of the CALL campaign. An imported lead arrived from a CSV
# and has none, so requiring wiring would make it unreachable by every drip -
# silently, since there is no campaign to point at as the missing piece and no
# error anywhere.
#
# That asymmetry is deliberate: for a lead with no call campaign the gate is the
# only condition. Expressed as `campaign_id IS NULL` rather than lead_source,
# because lead_source RECORDS where a lead came from while the absence of a
# campaign is the structural fact that decides - a lead given a phone and moved
# onto a campaign is wired by it from then on, whatever its provenance says.
TARGET = 'api/drip.py'
EXPECT = 'test_an_imported_lead_enters_on_the_GATE_ALONE'
LABEL = 'require wiring for a lead that has no call campaign to wire it'
OLD = """                    AND src.default_drip_id = c.campaign_id)
         OR l.campaign_id IS NULL)
\"\"\""""
NEW = """                    AND src.default_drip_id = c.campaign_id))
\"\"\""""
