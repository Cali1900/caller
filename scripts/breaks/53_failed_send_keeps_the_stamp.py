# On a transport failure we do NOT know whether Brevo accepted it. Un-stamping
# emailed_at would let the next tick send a second copy to someone who may
# already have the first.
#
# Re-anchored: the send_failed audit now appears in BOTH send_one and
# send_manual, so the old anchor matched twice and the break could not be
# applied. This one includes the auto-send-specific activity line.
TARGET = 'api/sender.py'
EXPECT = 'test_a_transport_failure_does_not_unstamp'
LABEL = 'clear emailed_at when an AUTO send fails, so the next tick resends'
OLD = """                    _audit(cur, lead_id, to_email, 'send_failed',
                           str(result.get('detail'))[:400])
                    cur.execute(
                        \"\"\"INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'note','auto-send FAILED - needs a person',%s)\"\"\","""
NEW = """                    _audit(cur, lead_id, to_email, 'send_failed',
                           str(result.get('detail'))[:400])
                    cur.execute('UPDATE leads SET emailed_at=NULL WHERE lead_id=%s',
                                (lead_id,))
                    cur.execute(
                        \"\"\"INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s,'note','auto-send FAILED - needs a person',%s)\"\"\","""
