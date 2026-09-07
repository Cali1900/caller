TARGET = 'api/scorer.py'
EXPECT = 'test_out_of_range_scores_are_clamped_not_rejected'
LABEL = 'remove the 0-10 clamp the JSON schema cannot express'
OLD = "                 _clamp(data['outcome_score']), _clamp(data['agent_score']),"
NEW = "                 data['outcome_score'], data['agent_score'],"
