# ⚠️ AN ABSENT FIELD IS NOT A FIELD SET TO FALSE.
#
# An unchecked HTML checkbox posts NOTHING, so from a form "absent" does mean
# off - which is why the web handler always supplies `enabled` explicitly. But a
# programmatic caller (a test, a seed, a migration, a script) passes rows without
# it and means "a normal enabled step".
#
# Collapsing the two silently DISABLED EVERY STEP created outside the form. The
# first version of this code did exactly that and every drip test went red at
# once, which is the lucky version - the unlucky version is a seeded sequence
# that quietly never sends.
#
# Third time this shape has bitten: an absent retry ladder is not an empty one,
# an absent campaign type is not 'call', and now this.
TARGET = 'api/drip.py'
EXPECT = 'test_a_step_created_without_the_field_is_enabled'
LABEL = 'treat an absent enabled field as OFF, disabling every seeded step'
OLD = """    if v is None:
        return default"""
NEW = """    if v is None:
        return False"""
