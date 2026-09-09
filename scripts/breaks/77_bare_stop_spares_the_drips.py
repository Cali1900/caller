# stop() with no argument means "stop dialing". Unscoped it also stops every
# running drip - silently, and the only symptom is email that stops arriving.
TARGET = 'api/campaigns.py'
EXPECT = 'test_bare_stop_stops_dialing_and_leaves_the_drips_running'
LABEL = 'let a bare stop() stop the drips too'
OLD = """                cur.execute(\"\"\"UPDATE campaign_configs SET is_running=false
                                WHERE is_running AND type='call' RETURNING *\"\"\")"""
NEW = """                cur.execute(\"\"\"UPDATE campaign_configs SET is_running=false
                                WHERE is_running RETURNING *\"\"\")"""
