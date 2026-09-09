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
                                          state, segment, external_ref, website,
                                          tz_source, tz_needs_review,
                                          pool_status, status)
                       VALUES (%(company)s, %(phone_e164)s, %(timezone)s, %(city)s,
                               %(state)s, COALESCE(%(segment)s,'default'),
                               %(external_ref)s, %(website)s, %(tz_source)s,
                               %(tz_needs_review)s, 'pool', 'new')
                       -- THE PREDICATE IS REQUIRED. leads_phone_uniq became a
                       -- PARTIAL unique index in migration 038 (NULL phones for
                       -- email-only leads), and Postgres will not use a partial
                       -- index as an ON CONFLICT arbiter unless the statement
                       -- repeats its WHERE. Without it every call-list upload
                       -- fails with "no unique or exclusion constraint matching
                       -- the ON CONFLICT specification" - which is what the
                       -- suite caught the moment the index changed.
                       ON CONFLICT (phone_e164) WHERE phone_e164 IS NOT NULL
                       DO NOTHING""",
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


# ===========================================================================
# EMAIL-ONLY LEADS: a list of firms with addresses, no calls involved
# ===========================================================================
#
# A different REQUIRED set, not a relaxation of the same one. The call path's
# rules are unchanged: a call list still needs a phone and a timezone or state,
# and parse_csv() is untouched.
#
# company + email are required. website and demands_per_month are optional and
# both earn their place: the website is what autosend's DOMAIN check compares
# the address against - the ONE exclusion that is NOT relaxed for an import -
# and demands_per_month is the volume figure the forecast reads.
#
# NO PHONE, NO TIMEZONE. Neither is required and neither is invented. A blank
# timezone on a lead that cannot be dialled is honest; a guessed one is a value
# that looks authoritative and was never verified.
REQUIRED_EMAIL = ('company', 'email')
OPTIONAL_EMAIL = ('website', 'demands_per_month', 'dm_name', 'city', 'state',
                  'segment', 'external_ref')


def normalize_email(raw: str):
    """
    A lowercased address, or None.

    Deliberately SHALLOW. The real checks - MX, role addresses, disposable
    domains, and whether the domain matches the firm's website - already live in
    api/email_validation.py and run at SEND time through autosend.eligibility(),
    where they belong: a list is worth importing even if some addresses are bad,
    and the gate is what stops those going out. Validating hard here would reject
    rows the operator wanted kept and would put the same rules in two places.
    """
    e = (raw or '').strip().lower()
    if not e or e.count('@') != 1:
        return None
    local, _, domain = e.partition('@')
    if not local or '.' not in domain or domain.startswith('.') \
            or domain.endswith('.') or ' ' in e:
        return None
    return e


def _demands(raw: str):
    """(int|None, raw|None). Blank means NOT ANSWERED, never zero."""
    v = (raw or '').strip()
    if not v:
        return None, None
    digits = ''.join(ch for ch in v if ch.isdigit())
    if not digits:
        return None, v          # keep what they wrote; the number is unknown
    n = int(digits)
    return (n if 0 <= n <= 10000 else None), v


def parse_email_csv(text: str):
    """Returns (rows_ok, rejects). Never touches the database."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], [{'line': 0, 'reason': 'empty file'}]
    headers = {(h or '').strip().lower() for h in reader.fieldnames}
    missing = [c for c in REQUIRED_EMAIL if c not in headers]
    if missing:
        return [], [{'line': 0,
                     'reason': f'missing required column(s): {missing}. '
                               f'An email list needs company and email; phone '
                               f'and timezone are not required and not used.'}]

    ok, rejects, seen = [], [], set()
    for i, raw in enumerate(reader, start=2):      # line 1 is the header
        row = {(k or '').strip().lower(): (v or '').strip()
               for k, v in raw.items() if k}
        company = row.get('company', '')
        email = normalize_email(row.get('email', ''))
        if not company:
            rejects.append({'line': i, 'reason': 'blank company'}); continue
        if email is None:
            rejects.append({'line': i,
                            'reason': f'unusable email {row.get("email","")!r}'})
            continue
        # WITHIN THE FILE too, not just against the database. A list that repeats
        # an address would otherwise insert the first and silently skip the rest,
        # and the report would call them database duplicates.
        if email in seen:
            rejects.append({'line': i,
                            'reason': f'{email} appears earlier in this file'})
            continue
        seen.add(email)
        dpm, dpm_raw = _demands(row.get('demands_per_month', ''))
        ok.append({'company': company, 'dm_email': email,
                   'demands_per_month': dpm, 'demands_per_month_raw': dpm_raw,
                   **{k: (row.get(k) or None)
                      for k in ('website', 'dm_name', 'city', 'state',
                                'segment', 'external_ref')}})
    return ok, rejects


def upload_emails(text: str, drip_campaign_id=None):
    """
    Insert email-only leads into the POOL. Returns a report.

    lead_source='import' is the whole point: autosend's "not confirmed by the
    agent" and "no contact name" exclusions are source-aware, because for an
    imported lead the evidence is a person choosing to upload the file rather
    than an agent confirming an address on a call. Setting dm_email_confirmed
    would fake the call-sourced evidence instead of recording the real one.

    LANDS IN THE POOL, never queued and never on a drip by this function alone.
    Uploading has never been the thing that starts contact and it is not going to
    become it: `drip_campaign_id` is accepted so a batch CAN be routed in one
    act, and when it is given the leads are put on that drip explicitly and the
    activity line says so.

    DUPLICATE ADDRESSES ARE SKIPPED, NOT UPDATED - the same rule the phone list
    follows, for the same reason: re-uploading must not resurrect a lead that has
    since gone dnc, bounced or been archived. Checked against EVERY lead, not
    just imported ones, so importing a firm we already call does not create a
    second row for it.
    """
    rows, rejects = parse_email_csv(text)
    inserted = skipped = routed = 0
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO leads (company, dm_email, website, dm_name,
                                          city, state, segment, external_ref,
                                          demands_per_month,
                                          demands_per_month_raw,
                                          lead_source, pool_status, status)
                       SELECT %(company)s, %(dm_email)s, %(website)s, %(dm_name)s,
                              %(city)s, %(state)s, COALESCE(%(segment)s,'default'),
                              %(external_ref)s, %(demands_per_month)s,
                              %(demands_per_month_raw)s,
                              'import', 'pool', 'new'
                        WHERE NOT EXISTS (
                              SELECT 1 FROM leads l
                               WHERE lower(btrim(l.dm_email)) = %(dm_email)s)
                    RETURNING lead_id""", r)
                got = cur.fetchone()
                if got is None:
                    skipped += 1
                    continue
                inserted += 1
                lid = got['lead_id']
                cur.execute(
                    """INSERT INTO activity (lead_id, kind, summary, detail)
                       VALUES (%s,'uploaded','added to pool (email only)',%s)""",
                    (lid, f"{r['company']} <{r['dm_email']}> - no phone, so not "
                          f"dialable until a number and a timezone are added"))
                if drip_campaign_id:
                    from api import drip as _drip
                    if _drip.enter(cur, lid, drip_campaign_id):
                        routed += 1
    return {'parsed': len(rows), 'inserted': inserted, 'duplicates': skipped,
            'rejected': len(rejects), 'rejects': rejects[:50],
            'routed_to_drip': routed}
