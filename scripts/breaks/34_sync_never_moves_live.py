# v8 went live once because agent.update() applied to whatever draft was in the
# Retell dashboard. Refreshing the LIST must not be able to move the live
# version by a different route: the version a campaign points at is an operator
# choice, and a background refresh is not an operator.
TARGET = 'api/prompts.py'
EXPECT = 'test_a_sync_never_changes_which_version_is_live'
LABEL = 'let a sync point the campaign at the newest version'
OLD = """    return {'stage': stage, 'versions': seen, 'fetched': fetched}"""
NEW = """    if seen:
        from api import campaigns as _c, db as _db
        newest = max(v for v in known) if known else 0
        with _db.get_conn() as _conn:
            with _conn.cursor() as _cur:
                _cur.execute('UPDATE campaign_configs SET agent_l1_version = %s',
                             (max(newest, 16),))
    return {'stage': stage, 'versions': seen, 'fetched': fetched}"""
