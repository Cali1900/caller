TARGET = 'api/guards.py'
EXPECT = 'test_suppressed_after_claim_is_refused_before_dialing'
LABEL = 'remove the pre-dial suppression re-check'
OLD = """    with conn.cursor() as cur:
        cur.execute(
            'SELECT reason FROM suppression WHERE phone_e164 = %s',
            (phone_e164,),
        )
        row = cur.fetchone()
    if row is not None:
        raise DialRefused(f'{phone_e164} is suppressed ({row["reason"]})')"""
NEW = "    return"
