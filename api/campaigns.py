"""
Campaigns: NAMED CONFIGURATIONS.

A campaign is not a day and not a prompt version. It owns the prompt version,
the sender identity, the cap, the spacing, the calling windows and its notes.
Two campaigns can both run v9 - the version is a property of a campaign, never
the thing that identifies it.

ONE RUNS AT A TIME, and that is enforced by a unique partial index in the
database (one_running_campaign), not by this module being careful. A second
concurrent start is rejected by Postgres.

THE SWITCH IS NEVER SILENT. start() refuses while another campaign is running
and returns what is running instead, so the caller must come back with an
explicit "stop that one and start this one". There is no code path that swaps
running campaigns without being told to.

Leads are ASSIGNED to a campaign (leads.campaign_id) and QUEUED separately
(pool_status='active'). Switching which campaign runs reassigns nothing, so a
campaign resumes exactly where it left off.
"""

import datetime

from api import db

TEMPLATE_FIELDS = ('subject_with_name', 'subject_without',
                   'body_with_name', 'body_without')

CONFIG_FIELDS = ('name', 'notes', 'agent_l1_version', 'agent_l3_version',
                 'sender_email', 'sender_name', 'sender_company_line',
                 'daily_cap', 'max_concurrent', 'dial_interval_min',
                 'dial_interval_max') + TEMPLATE_FIELDS

# The copy a NEW campaign starts with. Held here rather than as a column
# DEFAULT so there is exactly one place in the running app that says what the
# starting copy is, and a test can read it.
DEFAULT_TEMPLATE = {
    'subject_with_name': 'Following up \u2014 spoke with your front desk',
    'subject_without': 'Quick follow-up from {{time_of_day}}',
    'body_with_name': '''Hi {{first_name}},

{{gatekeeper_name}} at your front desk pointed me your way \u2014 she said you're
the one who handles demand letters.

We built CounselorAI for PI firms \u2014 it drafts the full demand package
from the case records, with citations verified against a closed
library of published opinions. Most firms spend six to eight hours on
one; this takes about thirty minutes.

First one's free on a real file, no card. If it's not better than what
you'd have sent, you've lost fifteen minutes.

Sample demand, redacted: https://counselorai.io/#letter

Worth a look?

{{sender_name}}
CounselorAI

Reply "unsubscribe" and I'll take you off the list.
{{footer}}
''',
    'body_without': '''Hi {{first_name}},

I called your office {{time_of_day}} and your front desk pointed me your way
on demand letters.

We built CounselorAI for PI firms \u2014 it drafts the full demand package
from the case records, with citations verified against a closed
library of published opinions. Most firms spend six to eight hours on
one; this takes about thirty minutes.

First one's free on a real file, no card. If it's not better than what
you'd have sent, you've lost fifteen minutes.

Sample demand, redacted: https://counselorai.io/#letter

Worth a look?

{{sender_name}}
CounselorAI

Reply "unsubscribe" and I'll take you off the list.
{{footer}}
''',
}

DEFAULT_WINDOWS = [(d, d != 0 and d != 6, '09:00', '17:00') for d in range(7)]


class CampaignConflict(Exception):
    """Another campaign is running. Carries it so the caller can ask."""

    def __init__(self, running):
        self.running = running
        super().__init__(f'{running["name"]} is currently running')


def list_all():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*,
                          (SELECT count(*) FROM leads l
                            WHERE l.campaign_id = c.campaign_id)         AS leads_total,
                          (SELECT count(*) FROM leads l
                            WHERE l.campaign_id = c.campaign_id
                              AND l.pool_status = 'active')              AS leads_queued
                     FROM campaign_configs c
                    ORDER BY c.is_running DESC, c.name""")
            return cur.fetchall()


def get(campaign_id):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM campaign_configs WHERE campaign_id = %s',
                        (campaign_id,))
            return cur.fetchone()


def running():
    """The one running campaign, or None. Everything the dialer needs."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM campaign_configs WHERE is_running')
            return cur.fetchone()


def create(name: str, template_from=None, **overrides):
    """
    A new campaign, seeded Mon-Fri 09:00-17:00.

    An empty week is a campaign that silently dials nothing, which is worse
    than a default that has to be changed.
    """
    base = get(template_from) if template_from else None
    vals = {
        'name': name.strip(),
        'notes': overrides.get('notes') or '',
        'agent_l1_version': (base or {}).get('agent_l1_version', 9),
        'agent_l3_version': (base or {}).get('agent_l3_version', 1),
        'sender_email': (base or {}).get('sender_email', 'sean@counselorai.io'),
        'sender_name': (base or {}).get('sender_name', 'Sean'),
        'sender_company_line': (base or {}).get('sender_company_line',
                                                'CounselorAI LLC'),
        'daily_cap': (base or {}).get('daily_cap', 100),
        'max_concurrent': (base or {}).get('max_concurrent', 1),
        'dial_interval_min': (base or {}).get('dial_interval_min', 210),
        'dial_interval_max': (base or {}).get('dial_interval_max', 300),
    }
    # Copy the source campaign's copy when cloning, else the default copy.
    for f in TEMPLATE_FIELDS:
        vals[f] = (base or {}).get(f) or DEFAULT_TEMPLATE[f]
    vals.update({k: v for k, v in overrides.items() if k in CONFIG_FIELDS and v is not None})
    if not vals['name']:
        raise ValueError('a campaign needs a name')

    cols = ', '.join(vals)
    ph = ', '.join(['%s'] * len(vals))
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'INSERT INTO campaign_configs ({cols}) VALUES ({ph}) RETURNING *',
                list(vals.values()))
            row = cur.fetchone()
            src = windows_for(row['campaign_id'], cur=cur, source=template_from)
            for dow, enabled, start, end in src:
                cur.execute(
                    """INSERT INTO campaign_windows
                           (campaign_id, dow, enabled, start_time, end_time)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (row['campaign_id'], dow, enabled, start, end))
            return row


def windows_for(campaign_id, cur=None, source=None):
    """Rows to seed a new campaign's week - copied from a template, else the
    Mon-Fri default."""
    if source and cur is not None:
        cur.execute("""SELECT dow, enabled, start_time, end_time
                         FROM campaign_windows WHERE campaign_id = %s
                        ORDER BY dow""", (source,))
        rows = cur.fetchall()
        if rows:
            return [(r['dow'], r['enabled'], r['start_time'], r['end_time'])
                    for r in rows]
    return DEFAULT_WINDOWS


def windows(campaign_id):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT * FROM campaign_windows
                            WHERE campaign_id = %s ORDER BY dow""", (campaign_id,))
            return cur.fetchall()


def set_windows(campaign_id, rows):
    """rows: {dow: (enabled, start, end)}"""
    changed = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for dow, (enabled, start, end) in rows.items():
                cur.execute(
                    """UPDATE campaign_windows
                          SET enabled=%s, start_time=%s::time, end_time=%s::time
                        WHERE campaign_id=%s AND dow=%s
                          AND (enabled, start_time, end_time)
                              IS DISTINCT FROM (%s, %s::time, %s::time)""",
                    (enabled, start, end, campaign_id, dow, enabled, start, end))
                if cur.rowcount:
                    changed.append(dow)
    return changed


def update(campaign_id, **fields):
    clean = {k: v for k, v in fields.items() if k in CONFIG_FIELDS}
    if not clean:
        return get(campaign_id)
    sets = ', '.join(f'{k} = %s' for k in clean)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'UPDATE campaign_configs SET {sets} WHERE campaign_id = %s RETURNING *',
                list(clean.values()) + [campaign_id])
            return cur.fetchone()


def start(campaign_id, stop_running: bool = False):
    """
    Start a campaign.

    If another is running this REFUSES and raises CampaignConflict carrying
    the running campaign, so the caller has to come back with
    stop_running=True after asking a person. There is deliberately no path
    that swaps silently.
    """
    current = running()
    if current and str(current['campaign_id']) != str(campaign_id):
        if not stop_running:
            raise CampaignConflict(current)
        stop(current['campaign_id'])
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE campaign_configs
                      SET is_running = true, started_at = now()
                    WHERE campaign_id = %s RETURNING *""", (campaign_id,))
            return cur.fetchone()


def stop(campaign_id=None):
    """Stop the given campaign, or whatever is running. Nothing dials after."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if campaign_id:
                cur.execute("""UPDATE campaign_configs SET is_running=false
                                WHERE campaign_id=%s RETURNING *""", (campaign_id,))
            else:
                cur.execute("""UPDATE campaign_configs SET is_running=false
                                WHERE is_running RETURNING *""")
            return cur.fetchone()


def assign(lead_ids, campaign_id):
    """Assign leads to a campaign. Does NOT queue them and never dials."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE leads SET campaign_id = %s, updated_at = now()
                    WHERE lead_id = ANY(%s::uuid[])""",
                (campaign_id, [str(i) for i in lead_ids]))
            return cur.rowcount


def campaign_date(cfg):
    """Today in the OPERATOR's timezone - used for the per-day new-lead count."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT (now() AT TIME ZONE %s)::date AS d',
                        (cfg.OPERATOR_TIMEZONE,))
            return cur.fetchone()['d']


def queue_stats(cfg, campaign_id):
    tz = cfg.OPERATOR_TIMEZONE
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                     count(*) FILTER (WHERE pool_status='active')            AS queued,
                     count(*) FILTER (WHERE pool_status='active'
                                        AND first_dialed_at IS NULL)         AS queued_new,
                     count(*) FILTER (WHERE pool_status='active'
                                        AND first_dialed_at IS NOT NULL
                                        AND status IN ('callback','no_answer','new','queued'))
                                                                             AS queued_carry,
                     count(*) FILTER (WHERE pool_status='pool')              AS pool,
                     count(*) FILTER (WHERE first_dialed_at IS NOT NULL
                                        AND (first_dialed_at AT TIME ZONE %s)::date
                                            = (now() AT TIME ZONE %s)::date) AS new_today
                   FROM leads WHERE campaign_id = %s""", (tz, tz, campaign_id))
            return cur.fetchone()
