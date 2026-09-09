# ONE STEP PER LEAD PER TICK.
#
# After a pause - a stopped drip, a worker outage, a sequence edited to bring
# delays forward - several steps can be due at the same instant. Sending them all
# puts two or three emails in front of one firm inside a minute, which reads as a
# malfunction and is the fastest way to get a sending domain blocked.
#
# The sequence must advance in ORDER and one rung at a time, exactly like the
# retry ladder. due() therefore returns the EARLIEST unsent due step per lead.
TARGET = 'api/drip.py'
EXPECT = 'test_only_the_earliest_unsent_due_step_is_selected'
LABEL = 'send every due step at once after a pause'
OLD = """            seen, out = set(), []
            for r in cur.fetchall():
                if r['lead_id'] in seen:
                    continue
                seen.add(r['lead_id'])
                out.append(r)
                if len(out) >= limit:
                    break
            return out


def upcoming"""
NEW = """            out = []
            for r in cur.fetchall():
                out.append(r)
                if len(out) >= limit:
                    break
            return out


def upcoming"""
