# The email copy is OPERATOR-EDITABLE. Matching one hardcoded URL is how the
# rewrite silently produced an untracked draft: Sean had already changed the
# link from '/#letter' to '/', and nothing complained.
TARGET = 'api/clicks.py'
EXPECT = 'test_any_counselorai_link_in_the_copy_is_rewritten'
LABEL = 'match only one hardcoded sample URL again'
OLD = """SAMPLE_LINK = re.compile(r'https?://(?:www\\.)?counselorai\\.io[^\\s<>"\\')]*')"""
NEW = """SAMPLE_LINK = re.compile(r'https://counselorai\\.io/\\#letter')"""
