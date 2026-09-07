"""
Weekly backup of the SUPPRESSION table, off this droplet.

Why this table and not the whole database: suppression is the only table where
losing a row causes real-world harm. A lost lead is an inconvenience; a lost
suppression row means calling somebody who explicitly told us to stop, at
$500-$1,500 per violation with no cap.

DESTINATION: DigitalOcean Spaces, keeping the last KEEP_LAST copies.

NOTE ON CREDENTIALS: the SPACES_* keys currently in caller's .env are
legalflow's - caller has none of its own. That is a known, deliberate
exception to the credential separation, recorded here so it is swapped rather
than forgotten when caller gets its own.

The dump is a restorable .sql, not a CSV: restoring a DNC list should be
`psql < file`, not a hand-written import at the moment you need it most.
"""

import datetime
import gzip

from api import db, mail


def build_dump() -> tuple:
    """Returns (filename, gzipped_sql_bytes, row_count). Restorable as-is."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT phone_e164, reason, source, created_at
                             FROM suppression ORDER BY created_at""")
            rows = cur.fetchall()

    stamp = datetime.datetime.now(datetime.UTC)
    lines = [
        '-- caller: suppression list backup',
        f'-- generated {stamp.isoformat()}',
        f'-- {len(rows)} row(s)',
        '--',
        '-- Restore:  psql -U <user> -d <db> -f this-file.sql',
        '-- Safe to re-run: ON CONFLICT DO NOTHING.',
        '',
        'CREATE TABLE IF NOT EXISTS suppression (',
        '    phone_e164  text PRIMARY KEY,',
        '    reason      text NOT NULL,',
        '    source      text,',
        '    created_at  timestamptz NOT NULL DEFAULT now()',
        ');',
        '',
    ]

    def q(v):
        if v is None:
            return 'NULL'
        return "'" + str(v).replace("'", "''") + "'"

    for r in rows:
        lines.append(
            'INSERT INTO suppression (phone_e164, reason, source, created_at) '
            f"VALUES ({q(r['phone_e164'])}, {q(r['reason'])}, {q(r['source'])}, "
            f"{q(r['created_at'])}) ON CONFLICT (phone_e164) DO NOTHING;")

    sql = '\n'.join(lines) + '\n'
    name = f'caller-suppression-{stamp:%Y-%m-%d}.sql.gz'
    return name, gzip.compress(sql.encode()), len(rows)


KEEP_LAST = 12


def _client(cfg):
    import boto3
    return boto3.client(
        's3', region_name=cfg.SPACES_REGION,
        endpoint_url=cfg.SPACES_ENDPOINT,
        aws_access_key_id=cfg.SPACES_KEY,
        aws_secret_access_key=cfg.SPACES_SECRET)


def upload(cfg, name: str, blob: bytes) -> str:
    key = cfg.SPACES_BACKUP_PREFIX + name
    _client(cfg).put_object(
        Bucket=cfg.SPACES_BUCKET, Key=key, Body=blob,
        ContentType='application/gzip',
        # PRIVATE. A DNC list must never be a public object - the recording
        # URLs already taught us that lesson.
        ACL='private')
    return key


def list_backups(cfg):
    r = _client(cfg).list_objects_v2(
        Bucket=cfg.SPACES_BUCKET, Prefix=cfg.SPACES_BACKUP_PREFIX)
    return sorted(r.get('Contents', []), key=lambda o: o['LastModified'],
                  reverse=True)


def prune(cfg, keep: int = KEEP_LAST):
    """Keep the last `keep`. Deletes only within our own prefix."""
    objs = list_backups(cfg)
    doomed = objs[keep:]
    for o in doomed:
        if not o['Key'].startswith(cfg.SPACES_BACKUP_PREFIX):
            continue          # never delete outside our prefix
        _client(cfg).delete_object(Bucket=cfg.SPACES_BUCKET, Key=o['Key'])
    return [o['Key'] for o in doomed]


def fetch(cfg, key: str) -> bytes:
    return _client(cfg).get_object(Bucket=cfg.SPACES_BUCKET, Key=key)['Body'].read()


def run(cfg) -> dict:
    """
    Dump -> Spaces -> prune. Never raises; a failed backup must be VISIBLE,
    which is why a failure also emails.
    """
    try:
        name, blob, n = build_dump()
    except Exception as exc:
        print(f'[backup] dump failed: {exc}', flush=True)
        return {'ok': False, 'detail': f'dump failed: {exc}'}

    try:
        key = upload(cfg, name, blob)
        pruned = prune(cfg)
    except Exception as exc:
        detail = f'{type(exc).__name__}: {exc}'
        print(f'[backup] upload FAILED: {detail}', flush=True)
        # A silent backup failure is the whole risk. Shout about it.
        mail.send(cfg, cfg.DIGEST_TO,
                  '[caller] SUPPRESSION BACKUP FAILED',
                  f'The weekly suppression backup did not upload.\n\n{detail}\n\n'
                  f'{n} row(s) were dumped but not stored off-droplet.\n')
        return {'ok': False, 'rows': n, 'detail': detail}

    print(f'[backup] suppression {n} rows -> {key} (pruned {len(pruned)})', flush=True)
    return {'ok': True, 'rows': n, 'key': key, 'pruned': pruned,
            'kept': len(list_backups(cfg))}
