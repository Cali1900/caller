# On a transport failure we do NOT know whether Brevo accepted it. Un-stamping
# emailed_at would let the next tick send a second copy to someone who may
# already have the first.
TARGET = 'api/sender.py'
EXPECT = 'test_a_transport_failure_does_not_unstamp'
LABEL = 'clear emailed_at when a send fails, so the next tick resends'
OLD = """                    _audit(cur, lead_id, to_email, 'send_failed',
                           str(result.get('detail'))[:400])"""
NEW = """                    _audit(cur, lead_id, to_email, 'send_failed',
                           str(result.get('detail'))[:400])
                    cur.execute('UPDATE leads SET emailed_at=NULL WHERE lead_id=%s',
                                (lead_id,))"""
