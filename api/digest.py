"""
The end-of-day digest. ONE EMAIL.

Not a dashboard, not a per-call alert stream. Ten minutes to read, once a day,
and the human decides what changes.

The two scores are reported SEPARATELY and never averaged together. The pair
is the diagnosis: high agent_score with low outcome_score means the list or
the ask is wrong, not the script.

Under each failure bucket sit the actual VERBATIM lines, most common first.
The percentage says THAT calls fail; the quote says WHAT was said, and only
the quote can be written against.
"""

import datetime

from api import campaigns, db, mail

TOP_FAILURES = 3
QUOTES_PER_FAILURE = 5

# Outcomes that are WINS. They must never appear under "TOP FAILURE" - the
# digest is read to decide what to change, and mislabelling the one call that
# worked as a failure points the operator at the wrong thing.
SUCCESS_OUTCOMES = ('gave_name_and_email', 'gave_name', 'demo_agreed',
                    'send_email', 'busy_callback', 'transferred')


def _stats(conn, date, tz):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT
                 count(*)                                              AS dialed,
                 count(*) FILTER (WHERE c.transcript IS NOT NULL
                                    AND c.transcript <> '')            AS reached,
                 count(*) FILTER (WHERE s.we_got @> ARRAY['name'])     AS names,
                 count(*) FILTER (WHERE s.we_got @> ARRAY['email'])    AS emails,
                 round(avg(s.agent_score)::numeric, 1)                 AS avg_agent,
                 round(avg(s.outcome_score)::numeric, 1)               AS avg_outcome,
                 count(*) FILTER (WHERE s.needs_human)                 AS flagged,
                 count(*) FILTER (WHERE s.call_id IS NULL)             AS unscored,
                 round(coalesce(sum(c.cost_cents), 0)::numeric, 1)     AS call_cents,
                 round(coalesce(sum(s.cost_cents), 0)::numeric, 1)     AS score_cents
               FROM calls c
               LEFT JOIN call_scores s ON s.call_id = c.call_id
              WHERE (c.created_at AT TIME ZONE %s)::date = %s::date""",
            (tz, date))
        return cur.fetchone()


def _failures(conn, date, tz):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.what_happened, count(*) AS n
                 FROM call_scores s JOIN calls c ON c.call_id = s.call_id
                WHERE (c.created_at AT TIME ZONE %s)::date = %s::date
                  AND NOT (s.what_happened = ANY(%s))
                GROUP BY s.what_happened ORDER BY n DESC LIMIT %s""",
            (tz, date, list(SUCCESS_OUTCOMES), TOP_FAILURES))
        return cur.fetchall()


def _wins(conn, date, tz):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.what_happened, count(*) AS n
                 FROM call_scores s JOIN calls c ON c.call_id = s.call_id
                WHERE (c.created_at AT TIME ZONE %s)::date = %s::date
                  AND s.what_happened = ANY(%s)
                GROUP BY s.what_happened ORDER BY n DESC""",
            (tz, date, list(SUCCESS_OUTCOMES)))
        return cur.fetchall()


def _quotes(conn, date, tz, what_happened):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.their_words, count(*) AS n
                 FROM call_scores s JOIN calls c ON c.call_id = s.call_id
                WHERE (c.created_at AT TIME ZONE %s)::date = %s::date
                  AND s.what_happened = %s
                  AND s.their_words IS NOT NULL AND s.their_words <> ''
                GROUP BY s.their_words ORDER BY n DESC, length(s.their_words)
                LIMIT %s""",
            (tz, date, what_happened, QUOTES_PER_FAILURE))
        return cur.fetchall()


def _deductions(conn, date, tz):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d AS deduction, count(*) AS n
                 FROM call_scores s JOIN calls c ON c.call_id = s.call_id,
                      unnest(s.agent_deductions) d
                WHERE (c.created_at AT TIME ZONE %s)::date = %s::date
                GROUP BY d ORDER BY n DESC""", (tz, date))
        return cur.fetchall()


def _rules(conn, date, tz):
    """
    Named rules from the AGENT'S OWN PROMPT that were broken today.

    Separate from deductions on purpose. A deduction is a generic fault
    ("repeated the same ask"); a broken rule is the prompt's OWN rule, by its
    own name. The difference tells you which fix applies: an unnamed fault
    means the prompt needs the rule, a named one means the model is ignoring a
    rule that is already there.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r AS rule, count(*) AS n
              FROM call_scores s
              JOIN calls c ON c.call_id = s.call_id
              CROSS JOIN LATERAL unnest(s.rules_violated) AS r
             WHERE (c.created_at AT TIME ZONE %s)::date = %s::date
             GROUP BY r ORDER BY n DESC, r""", (tz, date))
        return cur.fetchall()


def _flagged(conn, date, tz):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.call_id, l.company, s.what_happened, s.their_words
                 FROM call_scores s
                 JOIN calls c ON c.call_id = s.call_id
                 JOIN leads l ON l.lead_id = s.lead_id
                WHERE (c.created_at AT TIME ZONE %s)::date = %s::date
                  AND s.needs_human
                ORDER BY c.created_at LIMIT 20""", (tz, date))
        return cur.fetchall()


def build(cfg, date=None):
    date = date or campaigns.campaign_date(cfg)
    # The day boundary is the OPERATOR's, not UTC's. Comparing a timestamptz
    # against a bare ::date applies it in the session timezone, which silently
    # dropped every evening call from the digest between 17:00 Pacific and
    # midnight UTC - exactly the calls made at the end of a calling day.
    tz = cfg.OPERATOR_TIMEZONE
    with db.get_conn() as conn:
        st = _stats(conn, date, tz)
        fails = _failures(conn, date, tz)
        wins = _wins(conn, date, tz)
        deds = _deductions(conn, date, tz)
        flagged = _flagged(conn, date, tz)
        rules = _rules(conn, date, tz)
        quotes = {f['what_happened']: _quotes(conn, date, tz, f['what_happened'])
                  for f in fails}

    dialed = st['dialed'] or 0
    L = []
    L.append(f"CALLER - {date}")
    L.append("=" * 52)
    L.append("")
    L.append(f"{dialed} dialed  |  {st['reached'] or 0} reached a human  |  "
             f"{st['names'] or 0} names  |  {st['emails'] or 0} emails")
    L.append("")
    # Reported side by side, NEVER averaged into one number.
    L.append(f"avg agent_score   {st['avg_agent']   if st['avg_agent']   is not None else '-'}"
             "    <- what we control, drive to 10")
    L.append(f"avg outcome_score {st['avg_outcome'] if st['avg_outcome'] is not None else '-'}"
             "    <- the business metric, never 10 across the board")
    if st['avg_agent'] is not None and st['avg_outcome'] is not None:
        if st['avg_agent'] >= 8 and st['avg_outcome'] <= 3:
            L.append("")
            L.append("  ^ HIGH agent / LOW outcome: the script is doing its job.")
            L.append("    That pattern points at the LIST or the ASK, not the script.")
    L.append("")

    if dialed:
        for f in fails:
            pct = round(100.0 * f['n'] / dialed)
            L.append(f"TOP FAILURE - {f['what_happened']}, {f['n']} call"
                     f"{'' if f['n'] == 1 else 's'} ({pct}%)")
            for q in quotes.get(f['what_happened'], []):
                L.append(f'    "{q["their_words"]}"   x{q["n"]}')
            L.append("")

    if wins:
        L.append("WHAT WORKED")
        for w in wins:
            L.append(f"    {w['what_happened']:<24} {w['n']} call"
                     f"{'' if w['n'] == 1 else 's'}")
        L.append("")

    if deds:
        L.append("AGENT DEDUCTIONS")
        for d in deds:
            L.append(f"    {d['deduction']:<24} {d['n']} call"
                     f"{'' if d['n'] == 1 else 's'}")
        L.append("")

    if rules:
        L.append("PROMPT RULES BROKEN")
        L.append("  (named rules from the agent's own prompt - a rule that is"
                 " NOT here")
        L.append("   was either kept or does not exist in the prompt at all)")
        for r in rules:
            L.append(f"    {r['rule']:<34} {r['n']} call"
                     f"{'' if r['n'] == 1 else 's'}")
        L.append("")

    if flagged:
        L.append(f"{len(flagged)} call(s) flagged for a human:")
        for r in flagged:
            L.append(f"    {r['company']} - {r['what_happened']}")
            if r['their_words']:
                L.append(f'      "{r["their_words"]}"')
            L.append(f"      call_id {r['call_id']}")
        L.append("")

    if st['unscored']:
        L.append(f"!! {st['unscored']} call(s) UNSCORED - the scorer is failing. "
                 "Check score_attempts.last_error.")
        L.append("")

    total = float(st['call_cents'] or 0) + float(st['score_cents'] or 0)
    L.append(f"cost: ${total/100:.2f}  "
             f"(calls ${float(st['call_cents'] or 0)/100:.2f} + "
             f"scoring ${float(st['score_cents'] or 0)/100:.2f})")
    L.append("")
    L.append("The LLM reports. It does not change the script - you do.")

    subject = (f"Caller {date}: {dialed} dialed, {st['emails'] or 0} emails, "
               f"agent {st['avg_agent'] if st['avg_agent'] is not None else '-'}")
    return {'subject': subject, 'body': "\n".join(L), 'stats': dict(st), 'date': date}


def send(cfg, date=None, force: bool = False):
    """
    One email per day. Idempotent: a re-run does not double-send unless forced.
    """
    d = build(cfg, date)
    date = d['date']
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sent_at FROM digests WHERE digest_date = %s', (date,))
            row = cur.fetchone()
            if row and row['sent_at'] and not force:
                return {'skipped': 'already sent', 'date': str(date)}

    result = mail.send(cfg, cfg.DIGEST_TO, d['subject'], d['body'])
    import json as _json
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO digests (digest_date, sent_at, recipient, subject,
                                        body, stats, send_error)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (digest_date) DO UPDATE
                     SET sent_at=EXCLUDED.sent_at, subject=EXCLUDED.subject,
                         body=EXCLUDED.body, stats=EXCLUDED.stats,
                         send_error=EXCLUDED.send_error""",
                (date, (None if not result['ok'] else datetime.datetime.now(datetime.UTC)),
                 cfg.DIGEST_TO, d['subject'], d['body'],
                 _json.dumps(d['stats'], default=str),
                 None if result['ok'] else result['detail']))
    return {'sent': result['ok'], 'detail': result['detail'], 'date': str(date)}
