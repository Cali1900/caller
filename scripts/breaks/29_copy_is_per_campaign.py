# The email copy is a property of the campaign. Reading a module constant
# instead means two campaigns silently share one template - which removes most
# of the reason to have a second campaign.
TARGET = 'api/drafts.py'
EXPECT = 'test_two_campaigns_can_run_different_copy'
LABEL = 'read the copy from the module default instead of the campaign'
OLD = """    subject_tpl = campaign.get(sub_key) or campaigns_mod.DEFAULT_TEMPLATE[sub_key]
    body_tpl = campaign.get(body_key) or campaigns_mod.DEFAULT_TEMPLATE[body_key]"""
NEW = """    subject_tpl = campaigns_mod.DEFAULT_TEMPLATE[sub_key]
    body_tpl = campaigns_mod.DEFAULT_TEMPLATE[body_key]"""
