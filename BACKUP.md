# Suppression backup — the one table that matters

`suppression` is the only table where losing a row causes real-world harm: it
means calling somebody who explicitly told us to stop, at $500–$1,500 per
violation with no cap. A lost lead is an inconvenience; a lost suppression row
is a liability.

## What runs

`/etc/cron.d/caller-backup` — **Sundays 03:15 UTC**, outside any calling
window:

```
/root/caller/scripts/backup_suppression.sh
```

It dumps `suppression` as a **restorable .sql** (not a CSV), gzips it, uploads
to DO Spaces, and prunes to the **last 12**. If the upload fails it **emails an
alert** — a silent backup failure is the entire risk.

| | |
|---|---|
| bucket | `caller-backups-sfo3` (private, created for caller) |
| prefix | `caller/suppression/` |
| keep | last 12 |
| log | `/var/log/caller-backup.log` |

⚠️ **The `SPACES_*` credentials are currently legalflow's** — caller has none of
its own. This is a known exception to the credential separation, written down
so it gets swapped rather than forgotten. The bucket itself is caller-owned.

## Restore — TESTED, not assumed

`./scripts/test_restore.sh` proves it end to end: fetches the newest backup
from Spaces, restores into a scratch database, compares row-for-row against
live by md5, then drops the scratch db.

**Verified 2026-09-07:**

```
caller/suppression/caller-suppression-2026-09-07.sql.gz  (433 bytes gz)
live    : 3 rows  md5=020b7227a88e77476da537cdb9f7f447
restored: 3 rows  md5=020b7227a88e77476da537cdb9f7f447
RESTORE VERIFIED: content identical.
```

## Restoring for real

```bash
# 1. get the file
docker compose exec -T caller-api python -c "
import gzip; from api import backup; from api.config import load_config
cfg = load_config(); k = backup.list_backups(cfg)[0]['Key']
open('/tmp/r.sql','wb').write(gzip.decompress(backup.fetch(cfg, k))); print(k)"

# 2. apply it — safe to re-run, every INSERT is ON CONFLICT DO NOTHING
docker compose exec -T caller-api cat /tmp/r.sql \
  | docker compose exec -T caller-postgres psql -U calleruser -d caller_db
```

The dump is idempotent by design: restoring over a live table **adds back
missing rows and changes nothing else**, so it is safe to run when you are
unsure whether you need it.
