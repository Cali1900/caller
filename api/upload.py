"""
CSV upload.

UPLOADING NEVER DIALS. Rows land with pool_status='pool', and the dialer's
selection query requires pool_status='active' AND membership in a started
campaign. Two independent reasons a freshly uploaded lead cannot be called,
so forgetting one does not place calls.

Bad rows are REPORTED, not silently fixed and not silently dropped. A CSV of
law firms will contain "(424) 200-2295" and blank timezones; guessing at
either is how you dial the wrong number or call someone at 6am.
"""

import csv
import io
import re
from zoneinfo import ZoneInfo, available_timezones

from api import db, timezones

# timezone is NOT required: it is derived from state when absent. An explicit
# timezone column still WINS - it is the authoritative override.
REQUIRED = ('company', 'phone')
NEEDS_ONE_OF = ('timezone', 'state')
# `website` is optional but load-bearing: the auto-send domain check compares
# the captured email against it, and without it that check cannot run - so a
# lead with no website can only auto-send on a known free-mail address.
OPTIONAL = ('city', 'state', 'segment', 'external_ref', 'website')

_ZONES = available_timezones()


def normalize_phone(raw: str):
    """
    Return E.164 or None. Deliberately conservative: North American 10 and
    11 digit forms, or an already-plus-prefixed international number.
    Anything else is rejected rather than guessed at.
    """
    if not raw:
        return None
    s = raw.strip()
    had_plus = s.startswith('+')
    digits = re.sub(r'\D', '', s)
    if not digits:
        return None
    if had_plus:
        cand = '+' + digits
    elif len(digits) == 10:
        cand = '+1' + digits
    elif len(digits) == 11 and digits.startswith('1'):
        cand = '+' + digits
    else:
        return None
    # Must satisfy the same CHECK constraint the column enforces, so a bad
    # value fails here with a row number rather than as a batch error.
    return cand if re.fullmatch(r'\+[1-9][0-9]{7,14}', cand) else None


def validate_timezone(raw: str):
    """IANA name only. An offset is wrong twice a year."""
    if not raw:
        return None
    s = raw.strip()
    if s not in _ZONES:
        return None
    try:
        ZoneInfo(s)
    except Exception:
        return None
    return s


def parse_csv(text: str):
    """Returns (rows_ok, rejects). Never touches the database."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], [{'line': 0, 'reason': 'empty file'}]
    headers = {(h or '').strip().lower() for h in reader.fieldnames}
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        return [], [{'line': 0, 'reason': f'missing required column(s): {missing}'}]
    if not any(c in headers for c in NEEDS_ONE_OF):
        return [], [{'line': 0,
                     'reason': 'need a timezone column or a state column '
                               '(state is mapped to IANA; timezone overrides)'}]

    ok, rejects = [], []
    for i, raw in enumerate(reader, start=2):      # line 1 is the header
        row = {(k or '').strip().lower(): (v or '').strip()
               for k, v in raw.items() if k}
        company = row.get('company', '')
        phone = normalize_phone(row.get('phone', ''))
        raw_tz = row.get('timezone', '')
        state = row.get('state', '')

        if not company:
            rejects.append({'line': i, 'reason': 'blank company'}); continue
        if phone is None:
            rejects.append({'line': i, 'reason': f'unparseable phone {row.get("phone","")!r}'}); continue

        # An explicit timezone is authoritative. Only derive when absent.
        if raw_tz:
            tz = validate_timezone(raw_tz)
            if tz is None:
                rejects.append({'line': i,
                                'reason': f'invalid IANA timezone {raw_tz!r}'}); continue
            tz_source, needs_review = 'csv', False
        elif state:
            tz, tz_source, needs_review = timezones.for_state(state)
            if tz_source == 'default':
                rejects.append({'line': i,
                                'reason': f'unrecognised state {state!r} - '
                                          f'add a timezone column for this row'}); continue
        else:
            rejects.append({'line': i,
                            'reason': 'no timezone and no state'}); continue

        ok.append({'company': company, 'phone_e164': phone, 'timezone': tz,
                   'tz_source': tz_source, 'tz_needs_review': needs_review,
                   **{k: (row.get(k) or None) for k in OPTIONAL}})
    return ok, rejects


def upload(text: str):
    """
    Insert into the POOL. Returns a report.

    Duplicate phone numbers are skipped, not updated: re-uploading a list must
    not resurrect a lead that has since gone dnc or completed.
    """
    rows, rejects = parse_csv(text)
    inserted = 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO leads (company, phone_e164, timezone, city,
                                          state, segment, external_ref,
                                          tz_source, tz_needs_review,
                                          pool_status, status)
                       VALUES (%(company)s, %(phone_e164)s, %(timezone)s, %(city)s,
                               %(state)s, COALESCE(%(segment)s,'default'),
                               %(external_ref)s, %(tz_source)s,
                               %(tz_needs_review)s, 'pool', 'new')
                       ON CONFLICT (phone_e164) DO NOTHING""",
                    r)
                if cur.rowcount:
                    inserted += 1
                    cur.execute(
                        """INSERT INTO activity (lead_id, kind, summary, detail)
                           SELECT lead_id, 'uploaded', 'added to pool', %s
                             FROM leads WHERE phone_e164 = %s""",
                        (r['company'], r['phone_e164']))
    flagged = sum(1 for r in rows if r.get('tz_needs_review'))
    return {'parsed': len(rows), 'inserted': inserted,
            'duplicates': len(rows) - inserted,
            'rejected': len(rejects), 'rejects': rejects[:50],
            'timezone_needs_review': flagged}
