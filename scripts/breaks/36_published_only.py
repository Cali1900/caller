# A draft in the Retell dashboard is somebody mid-edit, not something you can
# dial. v8 went live by accident once; offering drafts in the picker is the
# same mistake with a different route.
TARGET = 'api/prompts.py'
EXPECT = 'test_only_published_versions_are_offered'
LABEL = 'offer unpublished Retell drafts in the version picker'
OLD = """                      AND (is_published OR agent_version = %s)"""
NEW = """                      AND (true OR agent_version = %s)"""
