# The prompt version had two homes: an AGENT_L1_VERSION env seed and the
# campaign row. The env copy was always the stale one - the picker writes the
# campaign, nothing wrote env - and it surfaced only when the campaign could
# not be read, which is precisely when guessing is least affordable.
#
# Restoring the fallback here must turn the refusal test red. A call that
# cannot name its prompt version makes every score after it unattributable.
TARGET = 'api/retell.py'
EXPECT = 'test_it_refuses_when_no_campaign_is_running'
LABEL = 'fall back to a default version when no campaign says which is live'
OLD = """    if not camp:
        raise ValueError(
            f'no campaign to read {key} from - refusing to dial. The prompt '
            f'version is a property of a campaign; there is no default.')"""
NEW = """    if not camp:
        camp = {key: 9}"""
