"""
PIPELINE STAGE — where a lead has got to, as a status the system sets and the
operator overrules.

Until now the forecast DERIVED "engaged" from clicked-or-replied. That worked
only as long as the derivation stayed true to what Sean meant, and it could
never express a stage nobody can compute - "won", or a demo he booked in a
phone call the app never saw.

THE SYSTEM ADVANCES, NEVER RETREATS. A click on a lead already at demo_booked
must not drag it back to engaged, and nothing automatic may overwrite a stage
a PERSON set. That asymmetry is the whole design: automation moves a lead
forward through stages it can observe, and a human moves it anywhere.
"""

# Forward order. Only these are auto-advanceable.
RANK = {'emailed': 1, 'engaged': 2, 'demo_booked': 3, 'won': 4}

# Statuses the system must never touch automatically.
#
#   human_review  the scorer asked for a PERSON. Auto-advancing past it would
#                 silently clear the one flag that says "look at this".
#   dnc           compliance. Nothing automatic moves a suppressed lead.
#   lost, lost_no_response, bad_email
#                 conclusions. A stray click on a bounced address is not a
#                 reason to declare the lead engaged again.
FROZEN = frozenset({'human_review', 'dnc', 'lost', 'lost_no_response',
                    'bad_email'})


def may_advance(current: str, target: str) -> bool:
    """True when the SYSTEM may move `current` to `target`."""
    if target not in RANK:
        return False
    if current in FROZEN:
        return False
    return RANK.get(current, 0) < RANK[target]


def advance(cur, lead_id, target: str, why: str) -> bool:
    """
    Move a lead forward, in the caller's transaction. Returns True if it moved.

    Writes to the timeline so an automatic move is distinguishable from a hand
    one - the same rule as every other status change.
    """
    cur.execute('SELECT status FROM leads WHERE lead_id = %s', (lead_id,))
    row = cur.fetchone()
    if row is None or not may_advance(row['status'], target):
        return False
    cur.execute('UPDATE leads SET status = %s, updated_at = now() WHERE lead_id = %s',
                (target, lead_id))
    cur.execute(
        """INSERT INTO activity (lead_id, kind, summary, detail)
           VALUES (%s,'status','status advanced by the system',%s)""",
        (lead_id, f"{row['status']} -> {target}  ({why})"))
    return True
