TARGET = 'api/stages.py'
EXPECT = 'test_clicking_twice_does_not_reset_the_follow_up'
LABEL = 'let "I emailed them" fire from any stage (double-click resets the follow-up)'
OLD = "                    WHERE lead_id = %s AND stage = 'L2'"
NEW = "                    WHERE lead_id = %s"
