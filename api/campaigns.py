"""
Campaigns: one per day. Nothing dials until START.

THE CAP IS A TOTAL, NOT A FRESH BUDGET. A cap of 200 with 50 carry-overs
means 50 carry-overs + 150 fresh = 200 dials, never 50 + 200.

The one asymmetry: if carry-overs ALONE meet or exceed the cap, they still all
enroll and ZERO fresh are added. A carry-over is a promise already made to a
person who asked to be called back; a fresh lead is a cold call. Promised
callbacks beat cold calls, so the cap bends for the promise and never for the
cold list.

CARRY-OVERS AUTO-ENROL. Nobody approves a callback. A receptionist who said
"try Tuesday" has already given the answer; making a human re-approve it is
how Tuesday gets missed.
"""

import datetime

from api import db

ROLLOVER_FLAG_DAYS = 5

# Carry-over sources, in dial priority order. Rollovers go first: a lead that
# was due yesterday and never got dialed has already waited longest.
CARRYOVER_PRIORITY = ('rollover', 'callback', 'retry')


def campaign_date(cfg) -> datetime.date:
    """
    Today, in the OPERATOR's timezone.

    Not UTC. The box runs UTC (correct - see the build brief), but a UTC day
    boundary lands mid-afternoon in Los Angeles, which would split a working
    day across two campaigns and make the cap meaningless.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT (now() AT TIME ZONE %s)::date AS d',
                        (cfg.OPERATOR_TIMEZONE,))
            return cur.fetchone()['d']


def ensure(cfg, date=None, daily_cap=200):
    date = date or campaign_date(cfg)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO campaigns (campaign_date, daily_cap)
                   VALUES (%s, %s)
                   ON CONFLICT (campaign_date) DO NOTHING""",
                (date, daily_cap),
            )
            cur.execute('SELECT * FROM campaigns WHERE campaign_date = %s', (date,))
            return cur.fetchone()


def start(cfg, date=None):
    """Nothing dials until this is called."""
    date = date or campaign_date(cfg)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE campaigns SET started_at = COALESCE(started_at, now()),
                                        paused = false
                    WHERE campaign_date = %s RETURNING *""", (date,))
            return cur.fetchone()


def pause(cfg, date=None):
    """Stops NEW dials on the next selection, and blocks any claimed-but-
    undialed lead at the pre-dial check. Calls already in flight finish."""
    date = date or campaign_date(cfg)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE campaigns SET paused = true WHERE campaign_date = %s RETURNING *',
                (date,))
            return cur.fetchone()


def resume(cfg, date=None):
    date = date or campaign_date(cfg)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE campaigns SET paused = false WHERE campaign_date = %s RETURNING *',
                (date,))
            return cur.fetchone()


def _enrol(cur, date, lead_ids, source):
    """
    Insert into campaign_leads and put the lead in play.

    Returns the ids that were ACTUALLY newly enrolled, not the count of ids
    considered. Callers rely on that distinction: rollover() increments a
    per-lead counter, and re-running enrolment must not inflate it.
    """
    newly = []
    for lid in lead_ids:
        cur.execute(
            """INSERT INTO campaign_leads (campaign_date, lead_id, source)
               VALUES (%s, %s, %s)
               ON CONFLICT (campaign_date, lead_id) DO NOTHING""",
            (date, lid, source))
        if cur.rowcount:
            newly.append(lid)
            # Enrolment is what takes a lead out of the pool. Upload never does.
            cur.execute(
                "UPDATE leads SET pool_status = 'active', updated_at = now() "
                "WHERE lead_id = %s", (lid,))
    return newly


def rollover(cfg, date=None):
    """
    Yesterday's undialed leads carry to today, at the FRONT of the queue.

    rollover_days counts consecutive days a lead was due and never reached.
    At ROLLOVER_FLAG_DAYS it is flagged for a human - a lead that has been
    due for five days running is telling you something (bad timezone, bad
    number, a window that never opens) and no amount of retrying fixes it.
    """
    date = date or campaign_date(cfg)
    prev = date - datetime.timedelta(days=1)
    # The target campaign must exist before anything can be enrolled into it -
    # campaign_leads has a FK to campaigns. rollover() runs on a worker tick,
    # potentially as the very first thing that happens on a new day.
    ensure(cfg, date)
    moved, flagged = 0, 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT cl.lead_id FROM campaign_leads cl
                    WHERE cl.campaign_date = %s AND cl.dialed_at IS NULL""",
                (prev,))
            ids = [r['lead_id'] for r in cur.fetchall()]
            # Only leads NEWLY enrolled get their counter bumped, so this is
            # safe to run on every worker tick.
            newly = _enrol(cur, date, ids, 'rollover')
            moved = len(newly)
            for lid in newly:
                cur.execute(
                    """UPDATE leads SET rollover_days = rollover_days + 1,
                                        updated_at = now()
                        WHERE lead_id = %s RETURNING rollover_days, company""",
                    (lid,))
                row = cur.fetchone()
                if row and row['rollover_days'] == ROLLOVER_FLAG_DAYS:
                    flagged += 1
                    cur.execute(
                        """INSERT INTO activity (lead_id, kind, summary, detail)
                           VALUES (%s, 'note', %s, %s)""",
                        (lid, f'due but undialed {ROLLOVER_FLAG_DAYS} days running',
                         'check timezone, number, and whether its window ever opens'))
    return {'rolled_over': moved, 'flagged': flagged}


def enroll(cfg, date=None, fresh_limit=None):
    """
    Carry-overs first and unconditionally, then fresh up to whatever the cap
    leaves. Returns the counts, so "why did only 12 fresh go in" is answerable.
    """
    date = date or campaign_date(cfg)
    camp = ensure(cfg, date)
    cap = camp['daily_cap']
    counts = {'rollover': 0, 'callback': 0, 'retry': 0, 'fresh': 0,
              'cap': cap, 'carryover_total': 0}

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # --- carry-overs: already-made promises, never capped away ---
            for source, statuses in (('callback', ('callback',)),
                                     ('retry', ('no_answer',))):
                cur.execute(
                    """SELECT l.lead_id FROM leads l
                        WHERE l.status = ANY(%s)
                          AND l.pool_status <> 'done'
                          AND NOT EXISTS (SELECT 1 FROM campaign_leads cl
                                           WHERE cl.campaign_date = %s
                                             AND cl.lead_id = l.lead_id)
                          AND NOT EXISTS (SELECT 1 FROM suppression s
                                           WHERE s.phone_e164 = l.phone_e164)""",
                    (list(statuses), date))
                counts[source] = len(_enrol(
                    cur, date, [r['lead_id'] for r in cur.fetchall()], source))

            cur.execute(
                """SELECT count(*) AS n FROM campaign_leads
                    WHERE campaign_date = %s AND source <> 'fresh'""", (date,))
            carry = cur.fetchone()['n']
            counts['carryover_total'] = carry

            # --- fresh: only what the cap leaves. Can be zero. ---
            room = max(0, cap - carry)
            if fresh_limit is not None:
                room = min(room, fresh_limit)
            if room > 0:
                cur.execute(
                    """SELECT l.lead_id FROM leads l
                        WHERE l.status = 'new' AND l.pool_status = 'pool'
                          AND NOT EXISTS (SELECT 1 FROM campaign_leads cl
                                           WHERE cl.campaign_date = %s
                                             AND cl.lead_id = l.lead_id)
                          AND NOT EXISTS (SELECT 1 FROM suppression s
                                           WHERE s.phone_e164 = l.phone_e164)
                        ORDER BY l.created_at
                        LIMIT %s""", (date, room))
                counts['fresh'] = len(_enrol(
                    cur, date, [r['lead_id'] for r in cur.fetchall()], 'fresh'))
    return counts


def status(cfg, date=None):
    date = date or campaign_date(cfg)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.campaign_date, c.daily_cap, c.started_at, c.paused,
                          count(cl.lead_id)                            AS enrolled,
                          count(cl.dialed_at)                          AS dialed,
                          -- count(cl.lead_id), not count(*): with a LEFT JOIN
                          -- and no rows, count(*) counts the null row as 1 and
                          -- reports "remaining: 1" for an empty campaign.
                          count(cl.lead_id) FILTER (WHERE cl.dialed_at IS NULL)
                                                                       AS remaining
                     FROM campaigns c
                     LEFT JOIN campaign_leads cl ON cl.campaign_date = c.campaign_date
                    WHERE c.campaign_date = %s
                    GROUP BY c.campaign_date, c.daily_cap, c.started_at, c.paused""",
                (date,))
            return cur.fetchone()
