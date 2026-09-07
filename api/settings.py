"""
Operator-editable runtime settings.

Spacing and the calling windows must be changeable WITHOUT a deploy. An env
var needs a container recreate, so env is the first-boot SEED only - the live
value lives in the settings table and the CRM edits it.

Reads are cached for a few seconds so the dialer loop does not hit the
database every tick, but a change takes effect within one tick either way.
"""

import time

from api import db

# key -> (default, caster, human label, low, high)
SPEC = {
    'max_concurrent':    (1,   int, 'calls in flight at once', 1, 10),
    'dial_interval_min': (210, int, 'min seconds between dials', 15, 3600),
    'dial_interval_max': (300, int, 'max seconds between dials', 15, 7200),
    # Sender identity lives here, not in env: counselorai.io now,
    # demandcounselor.com once warm, and that must be a field the operator
    # edits rather than a container recreate.
    'sender_email': ('sean@counselorai.io', str, 'from address', None, None),
    'sender_name':  ('Sean',                str, 'from name', None, None),
    'sender_company_line': ('CounselorAI LLC · [ADDRESS TBD]', str,
                           'footer line', None, None),
}

_cache = {'at': 0.0, 'values': None}
_TTL = 5.0


def all_settings(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache['values'] is not None and now - _cache['at'] < _TTL:
        return _cache['values']
    values = {k: v[0] for k, v in SPEC.items()}
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT key, value FROM settings')
                for r in cur.fetchall():
                    if r['key'] in SPEC:
                        values[r['key']] = SPEC[r['key']][1](r['value'])
    except Exception as exc:
        # A settings outage must not stop dialing at an UNKNOWN cadence - fall
        # back to the conservative defaults above, which are slower, not faster.
        print(f'[settings] read failed, using defaults: {exc}', flush=True)
    _cache.update(at=now, values=values)
    return values


def get(key: str):
    return all_settings()[key]


def set_many(pairs: dict, updated_by: str = 'operator') -> dict:
    """Validate then write. Refuses out-of-range values rather than clamping
    silently - a typo that halves the spacing should be visible."""
    errors = {}
    clean = {}
    for k, raw in pairs.items():
        if k not in SPEC:
            errors[k] = 'unknown setting'; continue
        default, cast, label, lo, hi = SPEC[k]
        try:
            v = cast(raw)
        except (TypeError, ValueError):
            errors[k] = 'not a number'; continue
        if cast is str:
            v = v.strip()
            if not v:
                errors[k] = 'cannot be blank'; continue
            if k == 'sender_email' and ('@' not in v or '.' not in v.split('@')[-1]):
                errors[k] = 'not an email address'; continue
        elif not (lo <= v <= hi):
            errors[k] = f'must be between {lo} and {hi}'; continue
        clean[k] = v

    if 'dial_interval_min' in clean or 'dial_interval_max' in clean:
        cur_vals = all_settings(force=True)
        lo = clean.get('dial_interval_min', cur_vals['dial_interval_min'])
        hi = clean.get('dial_interval_max', cur_vals['dial_interval_max'])
        if lo > hi:
            errors['dial_interval_min'] = 'min cannot exceed max'

    if errors:
        return {'ok': False, 'errors': errors}

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for k, v in clean.items():
                cur.execute(
                    """INSERT INTO settings (key, value, updated_by)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (key) DO UPDATE
                         SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by,
                             updated_at=now()""",
                    (k, str(v), updated_by))
    all_settings(force=True)
    return {'ok': True, 'set': clean}
