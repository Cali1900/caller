TARGET = 'api/digest.py'
EXPECT = 'test_send_is_idempotent_per_day'
LABEL = 'remove the once-per-day guard (double-send the digest)'
OLD = """            if row and row['sent_at'] and not force:
                return {'skipped': 'already sent', 'date': str(date)}"""
NEW = """            pass"""
