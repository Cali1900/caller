# NULL demands_per_month means SHE DID NOT ANSWER. Treating it as zero says the
# firm sends no demands, and every forecast built on it drifts down each time
# somebody declines - silently, and in the direction that looks like bad news
# rather than like a bug.
TARGET = 'api/volume.py'
EXPECT = 'test_a_non_answer_is_NULL_not_zero'
LABEL = 'parse a non-answer as zero instead of null'
OLD = """    for phrase in NON_ANSWERS:
        if phrase in text:
            return None, 'she did not know'"""
NEW = """    for phrase in NON_ANSWERS:
        if phrase in text:
            return 0, 'she did not know'"""
